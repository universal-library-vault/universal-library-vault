import gradio as gr
import os
import re
import html
import random
import pandas as pd
import json
import hashlib
import uuid
from datetime import date, datetime

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse

# =========================================================
# PROJECT PATHS — PRODUCTION
# =========================================================
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
PERSISTENT_DATA_DIR = Path(os.getenv("PERSISTENT_DATA_DIR", "/var/data"))

DAILY_WISDOM_CSV = str(DATA_DIR / "daily_wisdom.csv")
CATALOG_CSV = str(DATA_DIR / "Forbidden_Library_Master_Catalog.csv")
PROMO_CSV = str(DATA_DIR / "promo_campaigns.csv")
USER_DB_JSON = str(PERSISTENT_DATA_DIR / "vault_users.json")


# =========================================================
# STRIPE CONFIG — SAFE TEST MODE (COLAB COMPATIBLE)
# =========================================================
import stripe
from urllib.parse import urlparse, parse_qs

# ✅ USE YOUR TEST KEYS ONLY
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "").strip()
STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID", "").strip()
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
APP_BASE_URL = os.getenv("APP_BASE_URL", "").strip().rstrip("/")

stripe.api_key = STRIPE_SECRET_KEY


# =========================================================
# FREE ACCESS / USAGE LIMITS
# =========================================================
MAX_FREE_OPENS_PER_DAY = 3
PREVIEW_PAGE_LIMIT = 10
MAX_FREE_FAVORITES = 3
DEV_MODE = False
PREMIUM_STORAGE_KEY = "vault_premium_entitlement"

def make_usage_state():
    return {
        "date": date.today().isoformat(),
        "opens_today": 0,
        "favorites": []
    }

def make_membership_state():
    return {
        "is_premium": False,
        "premium_tier": "free",
        "customer_id": "",
        "subscription_id": "",
        "subscription_status": "inactive",
        "expires_at": "",
        "last_verified_at": "",
        "last_handled_session_id": "",
        "restored_via_email": ""
    }

def make_user_session_state():
    return {
        "logged_in": False,
        "user_id": "",
        "email": "",
        "tier": "guest"
    }

def normalize_user_session_state(session):
    base = make_user_session_state()

    if not session or not isinstance(session, dict):
        session = {}

    out = dict(base)
    out.update(session)

    out["logged_in"] = bool(out.get("logged_in", False))
    out["user_id"] = str(out.get("user_id", "")).strip()
    out["email"] = str(out.get("email", "")).strip().lower()
    out["tier"] = str(out.get("tier", "guest")).strip().lower() or "guest"

    if not out["logged_in"]:
        out["user_id"] = ""
        out["email"] = ""
        if out["tier"] not in {"guest", "free", "premium"}:
            out["tier"] = "guest"

    return out


def normalize_membership_state(membership):
    base = make_membership_state()

    if not membership or not isinstance(membership, dict):
        membership = {}

    out = dict(base)
    out.update(membership)

    out["is_premium"] = bool(out.get("is_premium", False))
    out["premium_tier"] = str(out.get("premium_tier", "free")).strip().lower() or "free"
    out["customer_id"] = str(out.get("customer_id", "")).strip()
    out["subscription_id"] = str(out.get("subscription_id", "")).strip()
    out["subscription_status"] = str(out.get("subscription_status", "inactive")).strip().lower() or "inactive"
    out["expires_at"] = str(out.get("expires_at", "")).strip()
    out["last_verified_at"] = str(out.get("last_verified_at", "")).strip()
    out["last_handled_session_id"] = str(out.get("last_handled_session_id", "")).strip()
    out["restored_via_email"] = str(out.get("restored_via_email", "")).strip().lower()

    if out["premium_tier"] not in {"free", "premium"}:
        out["premium_tier"] = "premium" if out["is_premium"] else "free"

    active_statuses = {
        "active",
        "trialing",
        "paid",
        "premium"
    }

    if out["subscription_status"] in active_statuses:
        out["is_premium"] = True
        out["premium_tier"] = "premium"

    if out["premium_tier"] == "premium":
        out["is_premium"] = True

    if not out["is_premium"]:
        out["premium_tier"] = "free"
        if out["subscription_status"] not in {
            "inactive", "canceled", "cancelled", "expired", "past_due", "unpaid"
        }:
            out["subscription_status"] = "inactive"

    return out

def normalize_usage_state(usage):
    today = date.today().isoformat()

    if not usage or not isinstance(usage, dict):
        usage = make_usage_state()

    if "date" not in usage:
        usage["date"] = today

    if "opens_today" not in usage:
        usage["opens_today"] = 0

    if "favorites" not in usage:
        usage["favorites"] = []

    if usage.get("date") != today:
        usage["date"] = today
        usage["opens_today"] = 0

    try:
        usage["opens_today"] = int(usage.get("opens_today", 0))
    except:
        usage["opens_today"] = 0

    if usage["opens_today"] < 0:
        usage["opens_today"] = 0

    return usage

def build_premium_cta_html(mode="upgrade", request: gr.Request = None):
    if mode == "required":
        title = "Premium Required"
        body = "This title requires premium access for full reading."
    else:
        title = "Upgrade to Premium"
        body = "Unlock unlimited full-book reading across the vault."

    benefits = """
    <ul style="margin-top:10px; padding-left:18px; line-height:1.6;">
      <li>Unlimited full-book access</li>
      <li>No daily reading limits</li>
      <li>Access to premium-only titles</li>
      <li>Priority future features</li>
    </ul>
    """

    checkout_url = create_stripe_checkout_url(request)
    has_checkout = bool(str(checkout_url or "").strip())

    if has_checkout:
        button_html = f"""
        <a href="{html.escape(checkout_url, quote=True)}" target="_self" rel="noopener noreferrer">
          <button style="
            background:#E22;
            border:none;
            color:white;
            padding:12px 22px;
            border-radius:12px;
            font-size:16px;
            font-weight:600;
            width:100%;
            max-width:280px;
            cursor:pointer;
            line-height:1.2;">
            <div style="font-weight:800; font-size:18px;">Upgrade to Premium</div>
            <div style="font-size:12px; opacity:0.92; margin-top:4px;">$4.99 / month</div>
          </button>
        </a>
        """
        checkout_note = ""
    else:
        button_html = """
        <button style="
          background:#555;
          border:none;
          color:white;
          padding:12px 22px;
          border-radius:12px;
          font-size:16px;
          font-weight:600;
          width:100%;
          max-width:280px;
          cursor:not-allowed;
          line-height:1.2;"
          disabled>
          <div style="font-weight:800; font-size:18px;">Upgrade to Premium</div>
          <div style="font-size:12px; opacity:0.92; margin-top:4px;">Checkout temporarily unavailable</div>
        </button>
        """
        checkout_note = """
        <div class="small_text" style="margin-top:10px;">
          Premium checkout could not be generated for this request.
        </div>
        """

    return f"""
    <div class="results_wrap">
      <div class="card">
        <div class="card_title">{title}</div>

        <div class="body_text">
          {body}
        </div>

        <div class="small_text" style="margin-top:8px;">
          Premium unlocks unlimited full-book access beyond the free daily limit.
        </div>

        {benefits}

        <div style="margin-top:16px; text-align:center;">
          {button_html}
        </div>

        {checkout_note}
      </div>
    </div>
    """

def build_membership_status_html(membership):
    membership = normalize_membership_state(membership)

    is_premium = membership["is_premium"]
    premium_tier = membership["premium_tier"]
    subscription_status = membership["subscription_status"]
    expires_at = membership["expires_at"]

    if is_premium:
        title = "Premium Active"
        body = "Unlimited full-book reading is unlocked."
    elif subscription_status in {"expired", "canceled", "cancelled", "past_due", "unpaid"}:
        title = "Premium Inactive"
        body = "Premium access is not currently active."
    else:
        title = "Free Tier"
        body = "You are currently using the free vault access tier."

    extra_lines = []

    extra_lines.append(f"Tier: <b>{html.escape(premium_tier.title())}</b>")

    if is_premium:
        status_label = "Active"
    elif premium_tier == "free":
        status_label = "Free Access"
    else:
        status_label = subscription_status.replace("_", " ").title()

    extra_lines.append(f"Status: <b>{html.escape(status_label)}</b>")

    if expires_at:
        extra_lines.append(f"Expires: <b>{html.escape(expires_at)}</b>")

    extra_html = "<br>".join(extra_lines)

    return f"""
    <div class="results_wrap">
      <div class="card">
        <div class="card_title">{title}</div>
        <div class="body_text">{body}</div>
        <div class="small_text" style="margin-top:10px;">
          {extra_html}
        </div>
      </div>
    </div>
    """

def build_access_status_html(usage):
    usage = normalize_usage_state(usage)

    opens_today = usage["opens_today"]
    remaining = max(0, MAX_FREE_OPENS_PER_DAY - opens_today)

    if opens_today < MAX_FREE_OPENS_PER_DAY:
        return f"""
        <div class="results_wrap">
          <div class="card">
            <div class="card_title">Reading Access</div>
            <div class="body_text">
              You have <b>{remaining}</b> free full-book open(s) remaining today.
            </div>
            <div class="small_text" style="margin-top:8px;">
              Opens used today: <b>{opens_today}/{MAX_FREE_OPENS_PER_DAY}</b>
            </div>
          </div>
        </div>
        """
    else:
        return f"""
        <div class="results_wrap">
          <div class="card">
            <div class="card_title">Preview Mode</div>
            <div class="body_text">
              You have used your <b>{MAX_FREE_OPENS_PER_DAY}</b> free full-book opens for today.
              Preview access is active where preview PDFs exist.
            </div>
            <div class="small_text" style="margin-top:10px;">
              Preview mode active: first {PREVIEW_PAGE_LIMIT} pages.
            </div>
          </div>
        </div>
        """

def restore_access_on_load(usage, membership):
    usage = normalize_usage_state(usage)
    membership = normalize_membership_state(membership)

    if membership.get("is_premium", False):
        access_html = """
        <div class="results_wrap">
          <div class="card">
            <div class="card_title">Premium Active</div>
            <div class="body_text">
              Full-book reading is unlocked.
            </div>
          </div>
        </div>
        """
    else:
        access_html = build_access_status_html(usage)

    return usage, access_html

def resolve_book_access(selected_book, usage, membership, request: gr.Request = None):
    usage = normalize_usage_state(usage)
    membership = normalize_membership_state(membership)

    if not selected_book:
        selected_book = {}

    title = str(selected_book.get("title", "")).strip()
    full_url = str(selected_book.get("full_url", "")).strip()
    preview_url = str(selected_book.get("preview_url", "")).strip()

    is_premium_only = bool(selected_book.get("is_premium_only", False))
    free_preview_enabled = bool(selected_book.get("free_preview_enabled", True))
    preview_ready = bool(selected_book.get("preview_ready", bool(preview_url)))

    has_full = bool(full_url)
    has_preview = bool(preview_url) and preview_ready
    is_premium = bool(membership.get("is_premium", False))

    if not has_full and not has_preview:
        return {
            "mode": "missing",
            "usage": usage,
            "viewer_url": "",
            "access_html": """
            <div class="results_wrap">
              <div class="card">
                <div class="card_title">Reading Access</div>
                <div class="body_text">No full or preview PDF link was available for this book.</div>
              </div>
            </div>
            """,
            "cta_html": ""
        }

    # PREMIUM USER
    if is_premium:
        if has_full:
            return {
                "mode": "premium_full",
                "usage": usage,
                "viewer_url": full_url,
                "access_html": """
                <div class="results_wrap">
                  <div class="card">
                    <div class="card_title">Premium Active</div>
                    <div class="body_text">
                      Full-book reading is unlocked.
                    </div>
                  </div>
                </div>
                """,
                "cta_html": ""   # prevents upsell duplication
            }

        if has_preview:
            return {
                "mode": "premium_preview_fallback",
                "usage": usage,
                "viewer_url": preview_url,
                "access_html": f"""
                <div class="results_wrap">
                  <div class="card">
                    <div class="card_title">Premium Active</div>
                    <div class="body_text">
                      Premium access is active. Full PDF is unavailable for this title, so preview mode is being used.
                    </div>
                    <div class="small_text" style="margin-top:10px;">
                      Preview mode active: first {PREVIEW_PAGE_LIMIT} pages.
                    </div>
                  </div>
                </div>
                """,
                "cta_html": ""
            }

    # FREE USER + PREMIUM-ONLY TITLE
    if is_premium_only:
        if free_preview_enabled and has_preview:
            return {
                "mode": "premium_only_preview",
                "usage": usage,
                "viewer_url": preview_url,
                "access_html": f"""
                <div class="results_wrap">
                  <div class="card">
                    <div class="card_title">Preview Mode</div>
                    <div class="body_text">
                      This title is premium-only for full access. Preview access is available.
                    </div>
                    <div class="small_text" style="margin-top:10px;">
                      Preview mode active: first {PREVIEW_PAGE_LIMIT} pages.
                    </div>
                  </div>
                </div>
                """,
                "cta_html": build_premium_cta_html("required", request)
            }

        return {
            "mode": "premium_only_blocked",
            "usage": usage,
            "viewer_url": "",
            "access_html": """
            <div class="results_wrap">
              <div class="card">
                <div class="card_title">Premium Required</div>
                <div class="body_text">
                  This title requires premium access.
                </div>
              </div>
            </div>
            """,
            "cta_html": build_premium_cta_html("required", request)
        }

    # FREE USER + NORMAL TITLE + UNDER DAILY LIMIT
    if usage["opens_today"] < MAX_FREE_OPENS_PER_DAY and has_full:
        usage["opens_today"] += 1

        return {
            "mode": "free_full",
            "usage": usage,
            "viewer_url": full_url,
            "access_html": f"""
            <div class="results_wrap">
              <div class="card">
                <div class="card_title">Reading Access</div>
                <div class="body_text">
                  Full access granted for today’s free tier use.
                </div>
                <div class="small_text" style="margin-top:8px;">
                  Opens used today: <b>{usage["opens_today"]}/{MAX_FREE_OPENS_PER_DAY}</b>
                </div>
              </div>
            </div>
            """,
            "cta_html": ""
        }

    # FREE USER + NORMAL TITLE + OVER DAILY LIMIT
    if free_preview_enabled and has_preview:
        return {
            "mode": "free_preview",
            "usage": usage,
            "viewer_url": preview_url,
            "access_html": f"""
            <div class="results_wrap">
              <div class="card">
                <div class="card_title">Preview Mode</div>
                <div class="body_text">
                  You have used your <b>{MAX_FREE_OPENS_PER_DAY}</b> free full-book opens for today.
                  Preview access is still available for this title.
                </div>
                <div class="small_text" style="margin-top:10px;">
                  Preview mode active: first {PREVIEW_PAGE_LIMIT} pages.
                </div>
                <div class="small_text" style="margin-top:6px;">
                  Upgrade to premium for unlimited full-book access.
                </div>
              </div>
            </div>
            """,
            "cta_html": build_premium_cta_html("upgrade", request)
        }

    return {
        "mode": "free_blocked",
        "usage": usage,
        "viewer_url": "",
        "access_html": """
        <div class="results_wrap">
          <div class="card">
            <div class="card_title">Preview Unavailable</div>
            <div class="body_text">
              Full free access has been used for today, and no preview is available for this title.
            </div>
          </div>
        </div>
        """,
        "cta_html": build_premium_cta_html("upgrade", request)
    }


def create_stripe_checkout_url(request: gr.Request = None):
    try:
        secret_key = str(STRIPE_SECRET_KEY or "").strip()
        price_id = str(STRIPE_PRICE_ID or "").strip()

        if not secret_key or not price_id:
            print("STRIPE CHECKOUT ERROR: missing STRIPE_SECRET_KEY or STRIPE_PRICE_ID")
            return ""

        stripe.api_key = secret_key

        request_base = str(_get_base_app_url(request) or "").strip().rstrip("/")
        env_base = str(APP_BASE_URL or "").strip().rstrip("/")

        base_app_url = request_base or env_base

        if not base_app_url:
            print("STRIPE CHECKOUT ERROR: missing base app url")
            return ""

        success_url = f"{base_app_url}/?stripe_success=1&session_id={{CHECKOUT_SESSION_ID}}"
        cancel_url = f"{base_app_url}/?stripe_cancel=1"

        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[
                {
                    "price": price_id,
                    "quantity": 1
                }
            ],
            success_url=success_url,
            cancel_url=cancel_url
        )

        return str(session.url or "").strip()

    except Exception as e:
        print("STRIPE CHECKOUT ERROR:", str(e))
        return ""


def apply_stripe_success(membership, checkout_session=None, subscription_obj=None, session_id=""):
    membership = normalize_membership_state(membership)

    membership["is_premium"] = True
    membership["premium_tier"] = "premium"
    membership["subscription_status"] = "active"
    membership["last_verified_at"] = date.today().isoformat()

    if checkout_session:
        membership["customer_id"] = str(checkout_session.get("customer", "") or "").strip()
        membership["subscription_id"] = str(checkout_session.get("subscription", "") or "").strip()

    if subscription_obj:
        membership["subscription_status"] = str(subscription_obj.get("status", "active") or "active").strip().lower()

    if session_id:
        membership["last_handled_session_id"] = str(session_id).strip()

    return membership

def build_restore_result_html(title, body, extra_lines=None):
    extra_lines = extra_lines or []
    extra_html = ""

    if extra_lines:
        extra_html = f"""
        <div class="small_text" style="margin-top:10px;">
          {'<br>'.join(extra_lines)}
        </div>
        """

    return f"""
    <div class="results_wrap">
      <div class="card">
        <div class="card_title">{html.escape(title)}</div>
        <div class="body_text">{html.escape(body)}</div>
        {extra_html}
      </div>
    </div>
    """


def restore_premium_access(restore_input, membership, user_session):
    membership = normalize_membership_state(membership)
    user_session = normalize_user_session_state(user_session)

    restore_value = str(restore_input or "").strip()
    restore_email = normalize_email(restore_value)

    if not restore_value:
        result_html = build_restore_result_html(
            "Restore Premium",
            "Enter your Stripe email, customer ID, or subscription ID to restore premium access."
        )
        return membership, membership, build_membership_status_html(membership), result_html, build_account_status_html(user_session, membership)


    try:
        subscription_obj = None
        customer_id = ""
        subscription_id = ""
        customer_email = ""
        matched_by = ""

        # DIRECT SUBSCRIPTION LOOKUP
        if restore_value.startswith("sub_"):
            subscription_obj = stripe.Subscription.retrieve(restore_value)
            subscription_id = str(subscription_obj.get("id", "") or "").strip()
            customer_id = str(subscription_obj.get("customer", "") or "").strip()
            matched_by = "subscription_id"

        # CUSTOMER LOOKUP
        elif restore_value.startswith("cus_"):
            customer_id = restore_value
            matched_by = "customer_id"

        # EMAIL LOOKUP
        elif "@" in restore_email and "." in restore_email:
            matched_by = "email"

            customers = stripe.Customer.list(
                email=restore_email,
                limit=20
            )

            matched_customers = []
            for cust in customers.data:
                cust_email = normalize_email(cust.get("email", ""))
                if cust_email == restore_email:
                    matched_customers.append(cust)

            chosen_customer = None
            chosen_subscription = None

            for cust in matched_customers:
                cust_id = str(cust.get("id", "") or "").strip()

                subscriptions = stripe.Subscription.list(
                    customer=cust_id,
                    status="all",
                    limit=20
                )

                active_like = []
                fallback = []

                for sub in subscriptions.data:
                    status = str(sub.get("status", "") or "").strip().lower()
                    if status in {"active", "trialing"}:
                        active_like.append(sub)
                    else:
                        fallback.append(sub)

                if active_like:
                    chosen_customer = cust
                    chosen_subscription = active_like[0]
                    break

                if fallback and chosen_subscription is None:
                    chosen_customer = cust
                    chosen_subscription = fallback[0]

            if chosen_customer:
                customer_id = str(chosen_customer.get("id", "") or "").strip()
                customer_email = normalize_email(chosen_customer.get("email", ""))
            if chosen_subscription:
                subscription_obj = chosen_subscription
                subscription_id = str(chosen_subscription.get("id", "") or "").strip()

        else:
            result_html = build_restore_result_html(
                "Restore Premium Failed",
                "Enter a valid Stripe email, customer ID (cus_...), or subscription ID (sub_...)."
            )
            return membership, membership, build_membership_status_html(membership), result_html, build_account_status_html(user_session, membership)

        # If we have a customer_id but not yet a subscription, fetch subscriptions now
        if customer_id and subscription_obj is None:
            subscriptions = stripe.Subscription.list(
                customer=customer_id,
                status="all",
                limit=20
            )

            active_like = []
            fallback = []

            for sub in subscriptions.data:
                status = str(sub.get("status", "") or "").strip().lower()
                if status in {"active", "trialing"}:
                    active_like.append(sub)
                else:
                    fallback.append(sub)

            if active_like:
                subscription_obj = active_like[0]
            elif fallback:
                subscription_obj = fallback[0]

            if subscription_obj:
                subscription_id = str(subscription_obj.get("id", "") or "").strip()

        if customer_id and not customer_email:
            try:
                customer_obj = stripe.Customer.retrieve(customer_id)
                customer_email = normalize_email(customer_obj.get("email", ""))
            except:
                customer_email = ""

        if not subscription_obj:
            result_html = build_restore_result_html(
                "Restore Premium Failed",
                "No matching Stripe subscription was found for that email or ID.",
                extra_lines=[
                    f"Matched by: <b>{html.escape(matched_by)}</b>" if matched_by else ""
                ]
            )
            return membership, membership, build_membership_status_html(membership), result_html, build_account_status_html(user_session, membership)

        subscription_status = str(subscription_obj.get("status", "") or "").strip().lower()

        if subscription_status not in {"active", "trialing"}:
            membership["is_premium"] = False
            membership["premium_tier"] = "free"
            membership["subscription_status"] = subscription_status or "inactive"
            membership["customer_id"] = customer_id
            membership["subscription_id"] = subscription_id
            membership["last_verified_at"] = date.today().isoformat()
            membership["restored_via_email"] = customer_email or restore_email

            result_html = build_restore_result_html(
                "Premium Not Active",
                "A Stripe subscription was found, but it is not currently active.",
                extra_lines=[
                    f"Matched by: <b>{html.escape(matched_by)}</b>" if matched_by else "",
                    f"Customer ID: <b>{html.escape(customer_id)}</b>" if customer_id else "",
                    f"Customer Email: <b>{html.escape(customer_email)}</b>" if customer_email else "",
                    f"Subscription ID: <b>{html.escape(subscription_id)}</b>" if subscription_id else "",
                    f"Subscription status: <b>{html.escape(subscription_status.title())}</b>"
                ]
            )
            return membership, membership, build_membership_status_html(membership), result_html, build_account_status_html(user_session, membership)

        membership["is_premium"] = True
        membership["premium_tier"] = "premium"
        membership["subscription_status"] = subscription_status
        membership["customer_id"] = customer_id
        membership["subscription_id"] = subscription_id
        membership["last_verified_at"] = date.today().isoformat()
        membership["restored_via_email"] = customer_email or restore_email

        matched_email_for_user = normalize_email(customer_email or restore_email)

        if matched_email_for_user:
            restored_user = upsert_restored_user_account(matched_email_for_user, membership)
            has_password = bool(str((restored_user or {}).get("password_hash", "")).strip())

            if has_password:
                user_session = user_record_to_session(restored_user)
            elif user_session.get("logged_in") and normalize_email(user_session.get("email", "")) == matched_email_for_user:
                user_session["tier"] = "premium"

        restored_user = find_user_by_email(matched_email_for_user) if matched_email_for_user else None
        has_password = bool(str((restored_user or {}).get("password_hash", "")).strip())

        extra_lines = [
            f"Matched by: <b>{html.escape(matched_by)}</b>" if matched_by else "",
            f"Customer ID: <b>{html.escape(customer_id)}</b>" if customer_id else "",
            f"Customer Email: <b>{html.escape(customer_email)}</b>" if customer_email else "",
            f"Subscription ID: <b>{html.escape(subscription_id)}</b>" if subscription_id else "",
            f"Subscription status: <b>{html.escape(subscription_status.title())}</b>"
        ]

        if not has_password:
            extra_lines.append("No password is set for this account yet. Use the Set Password button in Membership.")

        result_html = build_restore_result_html(
            "Premium Restored",
            "Your Stripe subscription was verified and premium access has been restored.",
            extra_lines=extra_lines
        )

        return membership, membership, build_membership_status_html(membership), result_html, build_account_status_html(user_session, membership)

    except Exception as e:
        result_html = build_restore_result_html(
            "Restore Premium Error",
            "Stripe verification failed during premium restore.",
            extra_lines=[html.escape(str(e))]
        )
        return membership, membership, build_membership_status_html(membership), result_html, build_account_status_html(user_session, membership)

def make_stripe_flash_state():
    return {
        "html": "",
        "consumed": False
    }

def normalize_stripe_flash_state(flash):
    if not flash or not isinstance(flash, dict):
        flash = make_stripe_flash_state()

    if "html" not in flash:
        flash["html"] = ""

    if "consumed" not in flash:
        flash["consumed"] = False

    flash["html"] = str(flash.get("html", "") or "")
    flash["consumed"] = bool(flash.get("consumed", False))

    return flash

def consume_stripe_flash(flash):
    flash = normalize_stripe_flash_state(flash)

    if flash["html"] and not flash["consumed"]:
        html_out = flash["html"]
        flash["consumed"] = True
        return flash, html_out

    return flash, ""

def _get_request_url_string(request: gr.Request):
    if request is None:
        return ""

    try:
        if hasattr(request, "url") and request.url:
            return str(request.url)
    except:
        pass

    try:
        if hasattr(request, "request") and getattr(request.request, "url", None):
            return str(request.request.url)
    except:
        pass

    return ""


def _parse_return_params_from_request(request: gr.Request):
    raw_url = _get_request_url_string(request)

    if not raw_url:
        return {
            "raw_url": "",
            "stripe_success": "",
            "stripe_cancel": "",
            "session_id": ""
        }

    parsed = urlparse(raw_url)
    params = parse_qs(parsed.query)

    return {
        "raw_url": raw_url,
        "stripe_success": str(params.get("stripe_success", [""])[0]).strip(),
        "stripe_cancel": str(params.get("stripe_cancel", [""])[0]).strip(),
        "session_id": str(params.get("session_id", [""])[0]).strip()
    }


def verify_stripe_return_and_restore_membership(membership, flash, request: gr.Request):
    membership = normalize_membership_state(membership)
    flash = normalize_stripe_flash_state(flash)
    membership_html = build_membership_status_html(membership)

    params = _parse_return_params_from_request(request)
    stripe_success = params["stripe_success"]
    stripe_cancel = params["stripe_cancel"]
    session_id = params["session_id"]
    already_handled = (
        bool(session_id) and
        str(membership.get("last_handled_session_id", "")).strip() == str(session_id).strip()
    )

    if already_handled:
        flash = normalize_stripe_flash_state(flash)
        flash["html"] = ""
        flash["consumed"] = True
        return membership, membership_html, membership, flash

    if already_handled:
        return membership, membership_html, membership, flash

    if stripe_cancel == "1":
        return_html = """
        <div class="results_wrap">
          <div class="card">
            <div class="card_title">Checkout Canceled</div>
            <div class="body_text">
              Stripe returned to the app after checkout was canceled.
            </div>
          </div>
        </div>
        """
        flash["html"] = return_html
        flash["consumed"] = False
        return membership, membership_html, membership, flash

    if stripe_success != "1":
        return membership, membership_html, membership, flash

    if not session_id:
        return_html = """
        <div class="results_wrap">
          <div class="card">
            <div class="card_title">Purchase Return Detected</div>
            <div class="body_text">
              Stripe returned to the app, but no checkout session ID was provided.
            </div>
          </div>
        </div>
        """
        flash["html"] = return_html
        flash["consumed"] = False
        return membership, membership_html, membership, flash

    try:
        checkout_session = stripe.checkout.Session.retrieve(session_id)

        session_status = str(checkout_session.get("status", "") or "").strip().lower()
        payment_status = str(checkout_session.get("payment_status", "") or "").strip().lower()
        subscription_id = str(checkout_session.get("subscription", "") or "").strip()

        subscription_obj = None
        subscription_status = ""

        if subscription_id:
            subscription_obj = stripe.Subscription.retrieve(subscription_id)
            subscription_status = str(subscription_obj.get("status", "") or "").strip().lower()

        verified = False

        if subscription_id:
            if session_status == "complete" and subscription_status in {"active", "trialing"}:
                verified = True
        else:
            if session_status == "complete" and payment_status == "paid":
                verified = True

        if verified:
            membership = apply_stripe_success(
                membership,
                checkout_session,
                subscription_obj,
                session_id=session_id
            )

            customer_id = str(checkout_session.get("customer", "") or "").strip()
            customer_email = _get_customer_email_from_customer_id(customer_id)

            if customer_email:
                _upsert_user_from_stripe_customer(
                    customer_id=customer_id,
                    email=customer_email,
                    subscription_id=str(checkout_session.get("subscription", "") or "").strip(),
                    subscription_status=str((subscription_obj or {}).get("status", "active") or "active").strip().lower(),
                    subscription_obj=subscription_obj
                )

            membership_html = build_membership_status_html(membership)

            session_tail = html.escape(session_id[-18:]) if session_id else ""
            sub_id = html.escape(membership.get("subscription_id", ""))
            cust_id = html.escape(membership.get("customer_id", ""))

            extra_lines = []
            extra_lines.append(f"Checkout session verified: <b>...{session_tail}</b>")

            if cust_id:
                extra_lines.append(f"Customer ID: <b>{cust_id}</b>")

            if sub_id:
                extra_lines.append(f"Subscription ID: <b>{sub_id}</b>")

            if membership.get("subscription_status"):
                extra_lines.append(
                    f"Subscription status: <b>{html.escape(str(membership['subscription_status']).title())}</b>"
                )

            return_html = f"""
            <div class="results_wrap">
              <div class="card">
                <div class="card_title">Premium Activated</div>
                <div class="body_text">
                  Stripe checkout was verified and premium access is now active.
                </div>
                <div class="small_text" style="margin-top:10px;">
                  {'<br>'.join(extra_lines)}
                </div>
              </div>
            </div>
            """

            flash["html"] = return_html
            flash["consumed"] = False
            return membership, membership_html, membership, flash

        status_lines = [
            f"Checkout status: <b>{html.escape(session_status or 'unknown')}</b>"
        ]

        if payment_status:
            status_lines.append(f"Payment status: <b>{html.escape(payment_status)}</b>")

        if subscription_status:
            status_lines.append(f"Subscription status: <b>{html.escape(subscription_status)}</b>")

        return_html = f"""
        <div class="results_wrap">
          <div class="card">
            <div class="card_title">Purchase Return Detected</div>
            <div class="body_text">
              Stripe returned to the app, but the checkout could not yet be verified as active premium.
            </div>
            <div class="small_text" style="margin-top:10px;">
              {'<br>'.join(status_lines)}
            </div>
          </div>
        </div>
        """

        flash["html"] = return_html
        flash["consumed"] = False
        return membership, membership_html, membership, flash

    except Exception as e:
        err = html.escape(str(e))
        return_html = f"""
        <div class="results_wrap">
          <div class="card">
            <div class="card_title">Stripe Verification Error</div>
            <div class="body_text">
              The app detected a Stripe return, but verification failed.
            </div>
            <div class="small_text" style="margin-top:10px;">
              {err}
            </div>
          </div>
        </div>
        """

        flash["html"] = return_html
        flash["consumed"] = False
        return membership, membership_html, membership, flash


def _get_base_app_url(request: gr.Request):
    if request is None:
        return ""

    headers = {}
    try:
        headers = dict(request.headers)
    except:
        headers = {}

    origin = str(headers.get("origin", "")).strip()
    if origin.startswith("http://") or origin.startswith("https://"):
        return origin.rstrip("/")

    referer = str(headers.get("referer", "")).strip()
    if referer.startswith("http://") or referer.startswith("https://"):
        parsed = urlparse(referer)
        return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")

    raw_url = _get_request_url_string(request)
    if raw_url.startswith("http://") or raw_url.startswith("https://"):
        parsed = urlparse(raw_url)
        return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")

    proto = str(headers.get("x-forwarded-proto", "https")).strip() or "https"
    host = str(headers.get("x-forwarded-host", headers.get("host", ""))).strip()

    if host:
        return f"{proto}://{host}".rstrip("/")

    return ""


def build_stripe_return_html(request: gr.Request):
    raw_url = _get_request_url_string(request)

    if not raw_url:
        return ""

    parsed = urlparse(raw_url)
    params = parse_qs(parsed.query)

    stripe_success = str(params.get("stripe_success", [""])[0]).strip()
    stripe_cancel = str(params.get("stripe_cancel", [""])[0]).strip()
    session_id = str(params.get("session_id", [""])[0]).strip()

    if stripe_success == "1":
        session_tail = html.escape(session_id[-18:]) if session_id else ""
        session_line = f"<div class='small_text' style='margin-top:10px;'>Checkout session received: <b>...{session_tail}</b></div>" if session_id else ""

        return f"""
        <div class="results_wrap">
          <div class="card">
            <div class="card_title">Purchase Return Detected</div>
            <div class="body_text">
              Stripe returned successfully to the app.
            </div>
            {session_line}
          </div>
        </div>
        """

    if stripe_cancel == "1":
        return """
        <div class="results_wrap">
          <div class="card">
            <div class="card_title">Checkout Canceled</div>
            <div class="body_text">
              Stripe returned to the app after checkout was canceled.
            </div>
          </div>
        </div>
        """

    return ""


# =========================================================
# LOCKED DISPLAY TEXT
# =========================================================
BROWSE_HINT = "Type here. Browse the Library Vault by letter, title, keyword, or doctrine."

# =========================================================
# DISPLAY CLEANERS
# =========================================================
def _clean_display_text(text: str) -> str:
    t = html.unescape(str(text or ""))

    replacements = {
        "â€™": "'",
        "â€œ": '"',
        "â€": '"',
        "â€": '"',
        "â€“": "-",
        "â€”": "—",
        "â€¦": "...",
        "Â ": " ",
        "Â": "",
        "Ã©": "e",
        "Ã¨": "e",
        "Ã": "",
        "�": "",
    }

    for bad, good in replacements.items():
        t = t.replace(bad, good)

    t = re.sub(r"[øØðÐþÞœŒæÆ]+", "", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)

    return t.strip()

def normalize_space(text):
    text = str(text or "").replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text

def parse_bool(val, default=False):
    if val is None:
        return default

    s = str(val).strip().lower()

    if s in {"true", "1", "yes", "y", "active"}:
        return True

    if s in {"false", "0", "no", "n", "inactive"}:
        return False

    return default

def normalize_email(text):
    return str(text or "").strip().lower()

def _hash_password(password):
    return hashlib.sha256(str(password or "").encode("utf-8")).hexdigest()

def _make_user_id():
    return f"usr_{uuid.uuid4().hex[:16]}"

def ensure_user_db():
    if not os.path.exists(USER_DB_JSON):
        with open(USER_DB_JSON, "w", encoding="utf-8") as f:
            json.dump({"users": []}, f, indent=2)

def load_user_db():
    ensure_user_db()
    with open(USER_DB_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        data = {"users": []}

    if "users" not in data or not isinstance(data["users"], list):
        data["users"] = []

    return data

def save_user_db(data):
    with open(USER_DB_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def _subscription_status_is_premium(status):
    status = str(status or "").strip().lower()
    return status in {"active", "trialing"}

def _extract_period_end_iso(subscription_obj):
    try:
        period_end = subscription_obj.get("current_period_end", None)
        if period_end:
            return datetime.utcfromtimestamp(int(period_end)).isoformat()
    except:
        pass
    return ""

def _sync_user_membership_by_email_or_customer(email="", customer_id="", subscription_id="", subscription_status="inactive", subscription_obj=None):
    email = normalize_email(email)
    customer_id = str(customer_id or "").strip()
    subscription_id = str(subscription_id or "").strip()
    subscription_status = str(subscription_status or "inactive").strip().lower()
    expires_at = _extract_period_end_iso(subscription_obj or {})

    data = load_user_db()
    updated = False

    for idx, user in enumerate(data["users"]):
        user_email = normalize_email(user.get("email", ""))
        user_customer_id = str(user.get("stripe_customer_id", "") or "").strip()
        user_subscription_id = str(user.get("stripe_subscription_id", "") or "").strip()

        matched = False

        if email and user_email == email:
            matched = True
        elif customer_id and user_customer_id == customer_id:
            matched = True
        elif subscription_id and user_subscription_id == subscription_id:
            matched = True

        if not matched:
            continue

        data["users"][idx]["tier"] = "premium" if _subscription_status_is_premium(subscription_status) else "free"
        data["users"][idx]["stripe_customer_id"] = customer_id or user_customer_id
        data["users"][idx]["stripe_subscription_id"] = subscription_id or user_subscription_id
        data["users"][idx]["subscription_status"] = subscription_status
        data["users"][idx]["updated_at"] = datetime.utcnow().isoformat()

        updated = True

    if updated:
        save_user_db(data)

    return updated

def _upsert_user_from_stripe_customer(customer_id="", email="", subscription_id="", subscription_status="inactive", subscription_obj=None):
    email = normalize_email(email)
    customer_id = str(customer_id or "").strip()
    subscription_id = str(subscription_id or "").strip()
    subscription_status = str(subscription_status or "inactive").strip().lower()

    if not email:
        return None

    existing = find_user_by_email(email)

    membership = make_membership_state()
    membership["is_premium"] = _subscription_status_is_premium(subscription_status)
    membership["premium_tier"] = "premium" if membership["is_premium"] else "free"
    membership["customer_id"] = customer_id
    membership["subscription_id"] = subscription_id
    membership["subscription_status"] = subscription_status
    membership["expires_at"] = _extract_period_end_iso(subscription_obj or {})
    membership["last_verified_at"] = datetime.utcnow().isoformat()
    membership["restored_via_email"] = email

    if existing:
        updated = upsert_restored_user_account(email, membership)
        return updated

    if _subscription_status_is_premium(subscription_status):
        created = upsert_restored_user_account(email, membership)
        return created

    return None

def _get_customer_email_from_customer_id(customer_id):
    customer_id = str(customer_id or "").strip()
    if not customer_id:
        return ""

    try:
        customer_obj = stripe.Customer.retrieve(customer_id)
        return normalize_email(customer_obj.get("email", ""))
    except:
        return ""

def _handle_subscription_state_change(subscription_obj):
    subscription_obj = subscription_obj or {}

    subscription_id = str(subscription_obj.get("id", "") or "").strip()
    customer_id = str(subscription_obj.get("customer", "") or "").strip()
    subscription_status = str(subscription_obj.get("status", "") or "inactive").strip().lower()

    customer_email = _get_customer_email_from_customer_id(customer_id)

    if customer_email:
        _upsert_user_from_stripe_customer(
            customer_id=customer_id,
            email=customer_email,
            subscription_id=subscription_id,
            subscription_status=subscription_status,
            subscription_obj=subscription_obj
        )

    _sync_user_membership_by_email_or_customer(
        email=customer_email,
        customer_id=customer_id,
        subscription_id=subscription_id,
        subscription_status=subscription_status,
        subscription_obj=subscription_obj
    )

def _handle_invoice_paid(invoice_obj):
    invoice_obj = invoice_obj or {}

    customer_id = str(invoice_obj.get("customer", "") or "").strip()
    subscription_id = str(invoice_obj.get("subscription", "") or "").strip()

    if not subscription_id:
        return

    try:
        subscription_obj = stripe.Subscription.retrieve(subscription_id)
        _handle_subscription_state_change(subscription_obj)
    except Exception as e:
        print("WEBHOOK invoice.paid subscription retrieve failed:", str(e))

def _handle_invoice_payment_failed(invoice_obj):
    invoice_obj = invoice_obj or {}

    customer_id = str(invoice_obj.get("customer", "") or "").strip()
    subscription_id = str(invoice_obj.get("subscription", "") or "").strip()

    customer_email = _get_customer_email_from_customer_id(customer_id)

    _sync_user_membership_by_email_or_customer(
        email=customer_email,
        customer_id=customer_id,
        subscription_id=subscription_id,
        subscription_status="past_due",
        subscription_obj=None
    )

def process_stripe_webhook_event(event):
    event_type = str(event.get("type", "") or "").strip()
    event_data = ((event.get("data") or {}).get("object") or {})

    print("STRIPE WEBHOOK EVENT:", event_type)

    if event_type in {
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
        "customer.subscription.paused",
        "customer.subscription.resumed",
    }:
        _handle_subscription_state_change(event_data)

    elif event_type == "invoice.paid":
        _handle_invoice_paid(event_data)

    elif event_type == "invoice.payment_failed":
        _handle_invoice_payment_failed(event_data)

    return True

def find_user_by_email(email):
    email = normalize_email(email)
    data = load_user_db()

    for user in data["users"]:
        if normalize_email(user.get("email", "")) == email:
            return user

    return None

def find_user_index_by_email(data, email):
    email = normalize_email(email)

    for idx, user in enumerate(data["users"]):
        if normalize_email(user.get("email", "")) == email:
            return idx

    return -1

def make_user_record(email, password):
    now = datetime.utcnow().isoformat()

    return {
        "user_id": _make_user_id(),
        "email": normalize_email(email),
        "password_hash": _hash_password(password),
        "tier": "free",
        "stripe_customer_id": "",
        "stripe_subscription_id": "",
        "subscription_status": "inactive",
        "favorites": [],
        "last_opened_book": "",
        "last_opened_page": 0,
        "last_opened_at": "",
        "created_at": now,
        "updated_at": now
    }

def user_record_to_session(user):
    tier = str(user.get("tier", "free")).strip().lower() or "free"

    return {
        "logged_in": True,
        "user_id": str(user.get("user_id", "")).strip(),
        "email": normalize_email(user.get("email", "")),
        "tier": tier
    }

def upsert_restored_user_account(email, membership):
    email = normalize_email(email)
    membership = normalize_membership_state(membership)
    data = load_user_db()
    idx = find_user_index_by_email(data, email)
    now = datetime.utcnow().isoformat()

    if idx >= 0:
        user = data["users"][idx]
    else:
        user = make_user_record(email, "")
        user["created_at"] = now
        data["users"].append(user)
        idx = len(data["users"]) - 1

    data["users"][idx]["email"] = email
    data["users"][idx]["tier"] = "premium" if membership.get("is_premium") else "free"
    data["users"][idx]["stripe_customer_id"] = str(membership.get("customer_id", "")).strip()
    data["users"][idx]["stripe_subscription_id"] = str(membership.get("subscription_id", "")).strip()
    data["users"][idx]["subscription_status"] = str(membership.get("subscription_status", "inactive")).strip()
    data["users"][idx]["updated_at"] = now

    save_user_db(data)
    return data["users"][idx]

def build_account_status_html(user_session, membership):
    user_session = normalize_user_session_state(user_session)
    membership = normalize_membership_state(membership)

    if user_session["logged_in"]:
        title = "Member Account"
        body = f"Signed in as {html.escape(user_session['email'])}."
        tier_line = f"Account tier: <b>{html.escape(user_session['tier'].title())}</b>"
    else:
        title = "Guest Access"
        body = "You are currently browsing without a signed-in member account."
        tier_line = "Account tier: <b>Guest</b>"

    premium_line = f"Premium status: <b>{'Active' if membership.get('is_premium') else 'Inactive'}</b>"

    return f"""
    <div class="results_wrap">
      <div class="card">
        <div class="card_title">{title}</div>
        <div class="body_text">{body}</div>
        <div class="small_text" style="margin-top:10px;">
          {tier_line}<br>
          {premium_line}
        </div>
      </div>
    </div>
    """

def _get_user_record_from_session(user_session):
    user_session = normalize_user_session_state(user_session)

    if not user_session.get("logged_in"):
        return None, -1, None

    email = normalize_email(user_session.get("email", ""))
    if not email:
        return None, -1, None

    data = load_user_db()
    idx = find_user_index_by_email(data, email)

    if idx < 0:
        return data, -1, None

    return data, idx, data["users"][idx]


def build_continue_reading_html(user_session):
    user_session = normalize_user_session_state(user_session)

    if not user_session.get("logged_in"):
        return """
        <div class="results_wrap">
          <div class="card">
            <div class="card_title">Continue Reading</div>
            <div class="body_text">
              Sign in to save your last opened book.
            </div>
          </div>
        </div>
        """

    data, idx, user = _get_user_record_from_session(user_session)

    if not user:
        return """
        <div class="results_wrap">
          <div class="card">
            <div class="card_title">Continue Reading</div>
            <div class="body_text">
              No saved reading history yet.
            </div>
          </div>
        </div>
        """

    last_book = str(user.get("last_opened_book", "")).strip()
    last_page = int(user.get("last_opened_page", 0) or 0)

    if not last_book:
        return """
        <div class="results_wrap">
          <div class="card">
            <div class="card_title">Continue Reading</div>
            <div class="body_text">
              No saved reading history yet.
            </div>
          </div>
        </div>
        """

    page_text = f"Last saved page: <b>{last_page}</b>" if last_page > 0 else "Last saved page: <b>Start</b>"

    return f"""
    <div class="results_wrap">
      <div class="card">
        <div class="card_title">Continue Reading</div>
        <div class="body_text">
          Resume: <b>{html.escape(last_book)}</b>
        </div>
        <div class="small_text" style="margin-top:10px;">
          {page_text}
        </div>
      </div>
    </div>
    """


def get_continue_reading_book(user_session):
    user_session = normalize_user_session_state(user_session)

    empty_book = {
        "title": "",
        "full_url": "",
        "preview_url": "",
        "preview_ready": False,
        "is_premium_only": False,
        "free_preview_enabled": True
    }

    data, idx, user = _get_user_record_from_session(user_session)
    if not user:
        return empty_book

    last_book = str(user.get("last_opened_book", "")).strip()
    if not last_book:
        return empty_book

    for book in catalog_books:
        if str(book.get("title", "")).strip().lower() == last_book.lower():
            return {
                "title": book.get("title", ""),
                "full_url": book.get("full_url", ""),
                "preview_url": book.get("preview_url", ""),
                "preview_ready": bool(book.get("preview_ready", False)),
                "is_premium_only": bool(book.get("is_premium_only", False)),
                "free_preview_enabled": bool(book.get("free_preview_enabled", True))
            }

    return empty_book


def save_continue_reading(selected_book, user_session):
    user_session = normalize_user_session_state(user_session)
    selected_book = selected_book or {}

    title = str(selected_book.get("title", "")).strip()

    if not user_session.get("logged_in") or not title:
        return build_continue_reading_html(user_session)

    data, idx, user = _get_user_record_from_session(user_session)
    if not user:
        return build_continue_reading_html(user_session)

    now = datetime.utcnow().isoformat()

    data["users"][idx]["last_opened_book"] = title
    data["users"][idx]["last_opened_page"] = 0
    data["users"][idx]["last_opened_at"] = now
    data["users"][idx]["updated_at"] = now

    save_user_db(data)

    return build_continue_reading_html(user_session)


def _favorite_limit_for_user(user_session, membership):
    user_session = normalize_user_session_state(user_session)
    membership = normalize_membership_state(membership)

    is_premium = bool(membership.get("is_premium")) or str(user_session.get("tier", "")).strip().lower() == "premium"
    return None if is_premium else MAX_FREE_FAVORITES


def _favorite_entry_from_book(selected_book):
    if not selected_book or not isinstance(selected_book, dict):
        return None

    title = str(selected_book.get("title", "")).strip()
    if not title:
        return None

    return {
        "title": title,
        "full_url": str(selected_book.get("full_url", "") or "").strip(),
        "preview_url": str(selected_book.get("preview_url", "") or "").strip(),
        "preview_ready": bool(selected_book.get("preview_ready", False)),
        "is_premium_only": bool(selected_book.get("is_premium_only", False)),
        "free_preview_enabled": bool(selected_book.get("free_preview_enabled", True))
    }


def build_favorites_status_html(user_session, membership):
    user_session = normalize_user_session_state(user_session)
    membership = normalize_membership_state(membership)

    if not user_session.get("logged_in"):
        return """
        <div class="results_wrap">
          <div class="card">
            <div class="card_title">Favorites</div>
            <div class="body_text">
              Sign in to save favorite books.
            </div>
            <div class="small_text" style="margin-top:10px;">
              Free members can save up to 3 books. Premium members can save unlimited books.
            </div>
          </div>
        </div>
        """

    data, idx, user = _get_user_record_from_session(user_session)
    favorites = []

    if user:
        favorites = list(user.get("favorites", []) or [])

    limit = _favorite_limit_for_user(user_session, membership)
    count = len(favorites)

    if limit is None:
        limit_line = "Favorite limit: <b>Unlimited</b>"
    else:
        limit_line = f"Favorite limit: <b>{count}/{limit}</b>"

    return f"""
    <div class="results_wrap">
      <div class="card">
        <div class="card_title">Favorites</div>
        <div class="body_text">
          You have <b>{count}</b> saved favorite book(s).
        </div>
        <div class="small_text" style="margin-top:10px;">
          {limit_line}
        </div>
      </div>
    </div>
    """


def load_favorites_for_ui(user_session, membership):
    user_session = normalize_user_session_state(user_session)
    membership = normalize_membership_state(membership)

    status_html = build_favorites_status_html(user_session, membership)

    if not user_session.get("logged_in"):
        return gr.update(choices=[], value=None), status_html

    data, idx, user = _get_user_record_from_session(user_session)

    if not user:
        return gr.update(choices=[], value=None), status_html

    favorites = list(user.get("favorites", []) or [])
    choices = [str(f.get("title", "")).strip() for f in favorites if str(f.get("title", "")).strip()]

    return gr.update(choices=choices, value=None), status_html


def save_selected_to_favorites(selected_book, user_session, membership):
    user_session = normalize_user_session_state(user_session)
    membership = normalize_membership_state(membership)

    if not user_session.get("logged_in"):
        favorites_update, status_html = load_favorites_for_ui(user_session, membership)
        result_html = build_restore_result_html(
            "Favorites Unavailable",
            "Sign in to save favorites."
        )
        return user_session, favorites_update, status_html, result_html

    favorite_entry = _favorite_entry_from_book(selected_book)
    if not favorite_entry:
        favorites_update, status_html = load_favorites_for_ui(user_session, membership)
        result_html = build_restore_result_html(
            "Save Favorite Failed",
            "Select a book first before saving it to favorites."
        )
        return user_session, favorites_update, status_html, result_html

    data, idx, user = _get_user_record_from_session(user_session)
    if not user:
        favorites_update, status_html = load_favorites_for_ui(user_session, membership)
        result_html = build_restore_result_html(
            "Save Favorite Failed",
            "No local member account was found for the signed-in user."
        )
        return user_session, favorites_update, status_html, result_html

    favorites = list(user.get("favorites", []) or [])
    title = favorite_entry["title"]

    existing_idx = -1
    for i, fav in enumerate(favorites):
        if str(fav.get("title", "")).strip().lower() == title.lower():
            existing_idx = i
            break

    if existing_idx >= 0:
        favorites[existing_idx] = favorite_entry
        message_title = "Favorite Updated"
        message_body = "This book was already in favorites, so its saved entry was refreshed."
    else:
        limit = _favorite_limit_for_user(user_session, membership)
        if limit is not None and len(favorites) >= limit:
            favorites_update, status_html = load_favorites_for_ui(user_session, membership)
            result_html = build_restore_result_html(
                "Favorite Limit Reached",
                f"Free members can save up to {MAX_FREE_FAVORITES} favorite books. Remove one first or upgrade to premium."
            )
            return user_session, favorites_update, status_html, result_html

        favorites.append(favorite_entry)
        message_title = "Favorite Saved"
        message_body = "The selected book was added to your favorites."

    data["users"][idx]["favorites"] = favorites
    data["users"][idx]["updated_at"] = datetime.utcnow().isoformat()
    save_user_db(data)

    favorites_update, status_html = load_favorites_for_ui(user_session, membership)
    result_html = build_restore_result_html(
        message_title,
        message_body,
        extra_lines=[f"Book: <b>{html.escape(title)}</b>"]
    )
    return user_session, favorites_update, status_html, result_html


def remove_selected_favorite(favorite_title, user_session, membership):
    user_session = normalize_user_session_state(user_session)
    membership = normalize_membership_state(membership)

    if not user_session.get("logged_in"):
        favorites_update, status_html = load_favorites_for_ui(user_session, membership)
        result_html = build_restore_result_html(
            "Remove Favorite Failed",
            "Sign in to manage favorites."
        )
        return user_session, favorites_update, status_html, result_html

    favorite_title = str(favorite_title or "").strip()
    if not favorite_title:
        favorites_update, status_html = load_favorites_for_ui(user_session, membership)
        result_html = build_restore_result_html(
            "Remove Favorite Failed",
            "Select a saved favorite first."
        )
        return user_session, favorites_update, status_html, result_html

    data, idx, user = _get_user_record_from_session(user_session)
    if not user:
        favorites_update, status_html = load_favorites_for_ui(user_session, membership)
        result_html = build_restore_result_html(
            "Remove Favorite Failed",
            "No local member account was found for the signed-in user."
        )
        return user_session, favorites_update, status_html, result_html

    favorites = list(user.get("favorites", []) or [])
    new_favorites = [
        fav for fav in favorites
        if str(fav.get("title", "")).strip().lower() != favorite_title.lower()
    ]

    if len(new_favorites) == len(favorites):
        favorites_update, status_html = load_favorites_for_ui(user_session, membership)
        result_html = build_restore_result_html(
            "Remove Favorite Failed",
            "That saved favorite could not be found."
        )
        return user_session, favorites_update, status_html, result_html

    data["users"][idx]["favorites"] = new_favorites
    data["users"][idx]["updated_at"] = datetime.utcnow().isoformat()
    save_user_db(data)

    favorites_update, status_html = load_favorites_for_ui(user_session, membership)
    result_html = build_restore_result_html(
        "Favorite Removed",
        "The selected book was removed from your favorites.",
        extra_lines=[f"Book: <b>{html.escape(favorite_title)}</b>"]
    )
    return user_session, favorites_update, status_html, result_html


def select_favorite_by_title(favorite_title, user_session):
    user_session = normalize_user_session_state(user_session)

    empty_book = {
        "title": "",
        "full_url": "",
        "preview_url": "",
        "preview_ready": False,
        "is_premium_only": False,
        "free_preview_enabled": True
    }

    if not user_session.get("logged_in"):
        return empty_book

    favorite_title = str(favorite_title or "").strip()
    if not favorite_title:
        return empty_book

    data, idx, user = _get_user_record_from_session(user_session)
    if not user:
        return empty_book

    favorites = list(user.get("favorites", []) or [])

    for fav in favorites:
        if str(fav.get("title", "")).strip().lower() == favorite_title.lower():
            return {
                "title": str(fav.get("title", "")).strip(),
                "full_url": str(fav.get("full_url", "") or "").strip(),
                "preview_url": str(fav.get("preview_url", "") or "").strip(),
                "preview_ready": bool(fav.get("preview_ready", False)),
                "is_premium_only": bool(fav.get("is_premium_only", False)),
                "free_preview_enabled": bool(fav.get("free_preview_enabled", True))
            }

    return empty_book

def sign_up_member_ui(email, password, confirm_password, user_session, membership):
    user_session_out, debug_out, account_html, result_html = sign_up_member(
        email,
        password,
        confirm_password,
        user_session,
        membership
    )
    favorites_update, favorites_status_html = load_favorites_for_ui(user_session_out, membership)
    continue_reading_html = build_continue_reading_html(user_session_out)
    return user_session_out, debug_out, account_html, result_html, favorites_update, favorites_status_html, continue_reading_html


def log_in_member_ui(email, password, user_session, membership):
    user_session_out, debug_out, account_html, result_html = log_in_member(email, password, user_session, membership)
    favorites_update, favorites_status_html = load_favorites_for_ui(user_session_out, membership)
    continue_reading_html = build_continue_reading_html(user_session_out)
    return user_session_out, debug_out, account_html, result_html, favorites_update, favorites_status_html, continue_reading_html


def log_out_member_ui(user_session, membership):
    user_session_out, debug_out, account_html, result_html = log_out_member(user_session, membership)
    favorites_update, favorites_status_html = load_favorites_for_ui(user_session_out, membership)
    continue_reading_html = build_continue_reading_html(user_session_out)
    return user_session_out, debug_out, account_html, result_html, favorites_update, favorites_status_html, continue_reading_html


def restore_premium_access_ui(restore_input, membership, user_session, restore_claim_state):
    membership_out, debug_out, membership_html, restore_html, account_html = restore_premium_access(
        restore_input,
        membership,
        user_session
    )

    restore_claim_state = normalize_restore_claim_state(restore_claim_state)
    refreshed_user_session = normalize_user_session_state(user_session)

    matched_email = normalize_email(membership_out.get("restored_via_email", ""))
    restored_user = None

    if matched_email:
        restored_user = find_user_by_email(matched_email)

    if restored_user and str(restored_user.get("password_hash", "")).strip():
        refreshed_user_session = user_record_to_session(restored_user)
        restore_claim_state = {
            "email": matched_email,
            "ready_for_password_claim": False
        }
    elif membership_out.get("is_premium") and matched_email:
        restore_claim_state = {
            "email": matched_email,
            "ready_for_password_claim": True
        }
    else:
        restore_claim_state = {
            "email": "",
            "ready_for_password_claim": False
        }

    favorites_update, favorites_status_html = load_favorites_for_ui(refreshed_user_session, membership_out)
    restore_claim_html = build_restore_password_claim_html(restore_claim_state)
    account_html = build_account_status_html(refreshed_user_session, membership_out)

    return (
        membership_out,
        debug_out,
        membership_html,
        gr.update(visible=True, value=restore_html),
        account_html,
        favorites_update,
        favorites_status_html,
        refreshed_user_session,
        restore_claim_state,
        gr.update(visible=bool(str(restore_claim_html).strip()), value=restore_claim_html)
    )

def restore_account_and_favorites_on_load(user_session, membership):
    user_session = normalize_user_session_state(user_session)
    membership = normalize_membership_state(membership)

    account_html = build_account_status_html(user_session, membership)
    favorites_update, favorites_status_html = load_favorites_for_ui(user_session, membership)
    continue_reading_html = build_continue_reading_html(user_session)

    return (
        user_session,
        account_html,
        user_session,
        favorites_update,
        favorites_status_html,
        continue_reading_html
    )

# =========================================================
# DAILY JEWEL SOURCE
# =========================================================
def pick_jewel_source():
    if os.path.exists(DAILY_WISDOM_CSV):
        return DAILY_WISDOM_CSV
    return None

def derive_book_author(row):
    author = ""
    book = ""

    if "Author" in row and pd.notna(row["Author"]):
        author = str(row["Author"]).strip()

    if "Book" in row and pd.notna(row["Book"]):
        book = str(row["Book"]).strip()

    if not author:
        author = "Unknown"

    if not book:
        book = "Unknown Source"

    return author, book

def load_jewel_df():
    jewel_source = pick_jewel_source()
    print("JEWEL SOURCE:", jewel_source)

    if not jewel_source:
        raise ValueError(f"No Daily Wisdom CSV found at: {DAILY_WISDOM_CSV}")

    df = pd.read_csv(jewel_source)
    df.columns = [str(c).strip() for c in df.columns]

    if "Quote" not in df.columns:
        raise ValueError("daily_wisdom.csv must contain a Quote column.")

    rows = []
    for _, row in df.iterrows():
        quote = normalize_space(row.get("Quote", ""))
        if not quote:
            continue

        author, book = derive_book_author(row)

        rows.append({
            "Quote": quote,
            "Author": author,
            "Book": book
        })

    out = pd.DataFrame(rows).drop_duplicates(subset=["Quote"]).reset_index(drop=True)

    if out.empty:
        raise ValueError("Daily Wisdom CSV loaded, but no usable quotes were found.")

    return out

# =========================================================
# JEWEL STATE
# =========================================================
def make_jewel_state(df):
    ids = list(df.index)
    random.shuffle(ids)
    return {
        "remaining": ids[:],
        "used": []
    }

def draw_unique_indices(state, count):
    if len(state["remaining"]) < count:
        refill = state["used"][:]
        random.shuffle(refill)
        state["remaining"].extend(refill)
        state["used"] = []

    picked = []
    while state["remaining"] and len(picked) < count:
        idx = state["remaining"].pop(0)
        picked.append(idx)
        state["used"].append(idx)
    return picked

def format_daily_jewel(row):
    quote = html.escape(str(row["Quote"]).strip())
    author = html.escape(str(row["Author"]).strip() or "Unknown")
    book = html.escape(str(row["Book"]).strip() or "Unknown Source")

    return f"""
    <div class="side_wrap">
      <div class="card">
        <div class="card_title">Daily Jewel</div>
        <div class="daily_jewel_quote_wrap">
          <div class="daily_jewel_quote">
            <span class="daily_jewel_quote_inline_mark">“</span>{quote}<span class="daily_jewel_quote_inline_mark">”</span>
          </div>
        </div>
        <div class="daily_jewel_meta">
          <div class="daily_jewel_author">{author}</div>
          <div class="daily_jewel_book">{book}</div>
        </div>
      </div>
    </div>
    """

def refresh_jewel(state):
    picked = draw_unique_indices(state, 1)

    if not picked:
        return (
            "<div class='side_wrap'><div class='card'><div class='card_title'>Daily Jewel</div><div class='body_text'>No jewels available.</div></div></div>",
            state
        )

    daily_idx = picked[0]
    daily_html = format_daily_jewel(jewel_df.loc[daily_idx])

    return daily_html, state

# =========================================================
# PROMO ROTATOR SYSTEM
# =========================================================

def _extract_drive_file_id_from_url(url):
    s = str(url or "").strip()

    patterns = [
        r"/file/d/([a-zA-Z0-9_-]+)",
        r"[?&]id=([a-zA-Z0-9_-]+)",
        r"/d/([a-zA-Z0-9_-]+)",
    ]

    for p in patterns:
        m = re.search(p, s)
        if m:
            return m.group(1)

    return ""


def build_pdf_viewer_html(pdf_url):
    pdf_url = str(pdf_url or "").strip()

    if not pdf_url:
        return ""

    file_id = _extract_drive_file_id_from_url(pdf_url)

    if file_id:
        embed_url = f"https://drive.google.com/file/d/{file_id}/preview"
    else:
        embed_url = pdf_url

    safe_embed = html.escape(embed_url, quote=True)
    safe_link = html.escape(pdf_url, quote=True)

    return f"""
    <div class="results_wrap">
      <div class="card">
        <div class="card_title">Reader</div>

        <div class="pdf_mobile_fallback">
          <a href="{safe_link}" target="_blank" rel="noopener noreferrer" class="pdf_fallback_btn">
            Open PDF in Browser
          </a>
        </div>

        <div class="pdf_viewer_wrap pdf_desktop_embed">
          <iframe
            src="{safe_embed}"
            class="pdf_viewer_iframe"
            allow="autoplay"
            loading="lazy">
          </iframe>
        </div>
      </div>
    </div>
    """

def _normalize_public_image_url(url):
    s = str(url or "").strip()

    if not s:
        return ""

    if "drive.google.com" in s:
        file_id = _extract_drive_file_id_from_url(s)
        if file_id:
            return f"https://drive.google.com/uc?export=view&id={file_id}"

    if s.startswith("http://") or s.startswith("https://"):
        return s

    return ""

def _normalize_target_url(url):
    s = str(url or "").strip()

    if not s:
        return ""

    if s.startswith("http://") or s.startswith("https://"):
        return s

    return ""

def _safe_int(val, default=5):
    try:
        n = int(float(str(val).strip()))
        return n if n > 0 else default
    except:
        return default

def load_promo_df():
    if not os.path.exists(PROMO_CSV):
        return pd.DataFrame(columns=[
            "Promo_ID", "Promo_Name", "Image_URL", "Target_URL",
            "Duration_Seconds", "Active", "Priority", "Start_Date",
            "End_Date", "Notes"
        ])

    df = pd.read_csv(PROMO_CSV)
    df.columns = [str(c).strip() for c in df.columns]

    required = [
        "Promo_ID", "Promo_Name", "Image_URL", "Target_URL",
        "Duration_Seconds", "Active", "Priority", "Start_Date", "End_Date"
    ]

    for col in required:
        if col not in df.columns:
            raise ValueError(f"promo_campaigns.csv is missing required column: {col}")

    today = date.today().isoformat()

    rows = []
    for _, row in df.iterrows():
        active = str(row.get("Active", "")).strip().lower()
        if active not in {"yes", "true", "1", "active"}:
            continue

        start_date = str(row.get("Start_Date", "")).strip()
        end_date = str(row.get("End_Date", "")).strip()

        if start_date and today < start_date:
            continue
        if end_date and today > end_date:
            continue

        image_url = _normalize_public_image_url(row.get("Image_URL", ""))
        target_url = _normalize_target_url(row.get("Target_URL", ""))

        if not target_url:
            continue

        rows.append({
            "Promo_ID": str(row.get("Promo_ID", "")).strip(),
            "Promo_Name": str(row.get("Promo_Name", "")).strip() or "Promotion",
            "Image_URL": image_url,
            "Target_URL": target_url,
            "Duration_Seconds": _safe_int(row.get("Duration_Seconds", 5), 5),
            "Priority": _safe_int(row.get("Priority", 999), 999),
            "Notes": str(row.get("Notes", "")).strip(),
        })

    out = pd.DataFrame(rows)

    if out.empty:
        return out

    out = out.sort_values(
        by=["Priority", "Promo_ID"],
        ascending=[True, True]
    ).reset_index(drop=True)

    return out


def make_promo_state():
    promo_df = load_promo_df()

    if promo_df.empty:
        return {
            "rows": [],
            "index": 0
        }

    rows = []
    for _, row in promo_df.iterrows():
        rows.append({
            "Promo_ID": str(row["Promo_ID"]).strip(),
            "Promo_Name": str(row["Promo_Name"]).strip(),
            "Image_URL": str(row["Image_URL"]).strip(),
            "Target_URL": str(row["Target_URL"]).strip(),
            "Duration_Seconds": _safe_int(row["Duration_Seconds"], 5)
        })

    return {
        "rows": rows,
        "index": 0
    }

def build_single_promo_html(promo):
    if not promo:
        return """
        <div class="promo_shell">
          <div class="card promo_card">
            <div class="card_title">Featured Promotion</div>
            <div class="promo_stage promo_stage_empty">
              <div class="promo_empty_text">Promo space available.</div>
            </div>
          </div>
        </div>
        """

    name = html.escape(str(promo.get("Promo_Name", "Promotion")).strip())
    image = html.escape(str(promo.get("Image_URL", "")).strip(), quote=True)
    target = html.escape(str(promo.get("Target_URL", "")).strip(), quote=True)

    if image:
        stage_inner = f'<img class="promo_img" src="{image}" alt="{name}">'
    else:
        stage_inner = f'<div class="promo_fallback" style="display:flex;">{name}</div>'

    return f"""
    <div class="promo_shell">
      <div class="card promo_card">
        <div class="card_title">Featured Promotion</div>
        <a class="promo_link" href="{target}" target="_blank" rel="noopener noreferrer">
          <div class="promo_stage">
            {stage_inner}
          </div>
          <div class="promo_meta">
            <div class="promo_name">{name}</div>
          </div>
        </a>
      </div>
    </div>
    """

def rotate_promo(promo_state):
    if not promo_state or not promo_state.get("rows"):
        empty_html = build_single_promo_html(None)
        return empty_html, {"rows": [], "index": 0}

    rows = promo_state["rows"]
    idx = promo_state.get("index", 0)

    if idx >= len(rows):
        idx = 0

    promo = rows[idx]
    html_out = build_single_promo_html(promo)

    next_idx = (idx + 1) % len(rows)

    return html_out, {
        "rows": rows,
        "index": next_idx
    }


# =========================================================
# MASTER CATALOG LOADER
# =========================================================
_catalog_df_cache = None

def load_catalog_df():
    global _catalog_df_cache

    if _catalog_df_cache is not None:
        return _catalog_df_cache

    if not os.path.exists(CATALOG_CSV):
        raise ValueError(f"Catalog CSV not found: {CATALOG_CSV}")

    df = pd.read_csv(CATALOG_CSV)
    df.columns = [str(c).strip() for c in df.columns]
    _catalog_df_cache = df
    return df

def _book_dedupe_key(book):
    b = str(book or "").lower().strip()
    b = re.sub(r"\(\d+\)$", "", b).strip()
    b = re.sub(r"[_\-]+", " ", b)
    b = re.sub(r"\.pdf$", "", b, flags=re.IGNORECASE)
    b = re.sub(r"\s+", " ", b).strip()
    return b

def _normalize_pdf_link(val):
    link = str(val or "").strip()

    if not link:
        return ""

    if "drive.google.com/file/d/" in link:
        return link

    if "drive.google.com/open?id=" in link:
        file_id = link.split("open?id=")[-1].split("&")[0].strip()
        if file_id:
            return f"https://drive.google.com/file/d/{file_id}/view?usp=sharing"

    if "drive.google.com/uc?id=" in link:
        file_id = link.split("uc?id=")[-1].split("&")[0].strip()
        if file_id:
            return f"https://drive.google.com/file/d/{file_id}/view?usp=sharing"

    if re.fullmatch(r"[A-Za-z0-9_-]{20,}", link):
        return f"https://drive.google.com/file/d/{link}/view?usp=sharing"

    if link.startswith("http://") or link.startswith("https://"):
        return link

    return ""

def _extract_drive_file_id(link):
    link = str(link or "").strip()

    m = re.search(r"/file/d/([A-Za-z0-9_-]+)", link)
    if m:
        return m.group(1)

    m = re.search(r"[?&]id=([A-Za-z0-9_-]+)", link)
    if m:
        return m.group(1)

    if re.fullmatch(r"[A-Za-z0-9_-]{20,}", link):
        return link

    return ""

def _build_drive_thumbnail_url(link):
    file_id = _extract_drive_file_id(link)
    if not file_id:
        return ""
    return f"https://drive.google.com/thumbnail?id={file_id}&sz=w1000"

def _safe_row_text(row, col):
    if col in row and pd.notna(row[col]):
        return str(row[col]).strip()
    return ""

def _normalize_cover_image(val):
    link = str(val or "").strip()

    if not link:
        return ""

    if link.startswith("http://") or link.startswith("https://"):
        return link

    return ""

def build_catalog_books():
    df = load_catalog_df()
    books = []
    seen = set()

    for _, row in df.iterrows():
        title = (
            _safe_row_text(row, "Title_Display")
            or _safe_row_text(row, "Title")
            or _safe_row_text(row, "PDF_File_Name")
        ).strip()

        if not title:
            continue

        title = re.sub(r"\.pdf$", "", title, flags=re.IGNORECASE).strip()
        dedupe_key = _book_dedupe_key(title)

        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        full_pdf_link = _normalize_pdf_link(
            _safe_row_text(row, "Full_PDF_Drive_Link")
            or _safe_row_text(row, "PDF_Drive_Link")
        )

        preview_pdf_link = _normalize_pdf_link(
            _safe_row_text(row, "Preview_PDF_Drive_Link")
        )

        # use real cover image first
        cover_link = _normalize_cover_image(_safe_row_text(row, "Cover_Image"))

        # fallback to drive thumbnail from full PDF if no cover image exists
        if not cover_link:
            cover_link = _build_drive_thumbnail_url(full_pdf_link)

        search_parts = [
            title,
            _safe_row_text(row, "Alternate Title"),
            _safe_row_text(row, "Author"),
            _safe_row_text(row, "Category"),
            _safe_row_text(row, "Subcategory"),
            _safe_row_text(row, "Tradition"),
            _safe_row_text(row, "Keywords"),
            _safe_row_text(row, "Short Description"),
            _safe_row_text(row, "Long Description"),
        ]

        is_premium_only = parse_bool(
            _safe_row_text(row, "Is_Premium_Only"),
            default=False
        )

        free_preview_enabled = parse_bool(
            _safe_row_text(row, "Free_Preview_Enabled"),
            default=True
        )

        preview_ready_raw = _safe_row_text(row, "Preview_Ready")
        preview_ready = parse_bool(
            preview_ready_raw,
            default=bool(preview_pdf_link)
        )

        books.append({
            "title": title,
            "title_low": title.lower(),
            "full_url": full_pdf_link,
            "preview_url": preview_pdf_link,
            "preview_ready": preview_ready,
            "is_premium_only": is_premium_only,
            "free_preview_enabled": free_preview_enabled,
            "link": full_pdf_link,
            "thumb": cover_link,
            "category": _safe_row_text(row, "Category"),
            "tradition": _safe_row_text(row, "Tradition"),
            "author": _safe_row_text(row, "Author"),
            "search_text": normalize_space(" ".join(search_parts)).lower()
        })

    return books

catalog_books = build_catalog_books()

def build_category_list():
    cats = []
    seen = set()

    for book in catalog_books:
        cat = str(book.get("category", "")).strip()
        if not cat:
            continue

        low = cat.lower()
        if low in seen:
            continue

        seen.add(low)
        cats.append(cat)

    return sorted(cats, key=lambda x: x.lower())[:16]

category_list = build_category_list()

def build_category_rail_html():
    if not category_list:
        return """
        <div class="category_wrap">
          <div class="category_empty">No categories available.</div>
        </div>
        """

    pills = []
    for cat in category_list:
        safe = html.escape(cat)
        pills.append(f"""
        <button class="category_pill" data-category="{safe}" type="button">{safe}</button>
        """)

    return f"""
    <div class="category_wrap">
      <div class="category_label">Explore by Category</div>
      <div class="category_row">
        {''.join(pills)}
      </div>
    </div>
    """
# =========================================================
# CATEGORY SYSTEM
# 8 centered vault categories
# =========================================================
CATEGORY_GROUPS = [
    ("Alchemy", ["alchemy", "alchemical"]),
    ("Hermeticism", ["hermetic", "hermeticism", "hermes", "kybalion"]),
    ("Magic", ["magic", "magick", "sorcery", "occult", "occultism"]),
    ("Mysticism", ["mysticism", "mystic", "gnosis", "gnostic"]),
    ("Religion", ["religion", "religious", "christian", "judaism", "islam", "bible", "god"]),
    ("Mythology", ["mythology", "myth", "egyptian", "greek", "roman", "norse"]),
    ("Early Science", ["science", "early science", "natural philosophy", "tesla", "physics"]),
    ("Philosophy", ["philosophy", "philosophical", "metaphysics", "ethics", "logic"]),
]

top_categories = [name for name, _ in CATEGORY_GROUPS]

def _book_matches_category(book, category_terms):
    fields = [
        str(book.get("title", "")),
        str(book.get("category", "")),
        str(book.get("tradition", "")),
        str(book.get("author", "")),
        str(book.get("search_text", "")),
    ]
    hay = " ".join(fields).lower()

    for term in category_terms:
        t = term.lower().strip()
        if not t:
            continue
        if re.search(rf"\b{re.escape(t)}\b", hay) or t in hay:
            return True

    return False

def select_book_by_title(title):
    clean_title = _clean_display_text(title).strip()

    if not clean_title:
        return {
            "title": "",
            "full_url": "",
            "preview_url": "",
            "preview_ready": False,
            "is_premium_only": False,
            "free_preview_enabled": True
        }

    for book in catalog_books:
        if str(book.get("title", "")).strip().lower() == clean_title.lower():
            return {
                "title": book.get("title", ""),
                "full_url": book.get("full_url", ""),
                "preview_url": book.get("preview_url", ""),
                "preview_ready": bool(book.get("preview_ready", False)),
                "is_premium_only": bool(book.get("is_premium_only", False)),
                "free_preview_enabled": bool(book.get("free_preview_enabled", True))
            }

    return {
        "title": "",
        "full_url": "",
        "preview_url": "",
        "preview_ready": False,
        "is_premium_only": False,
        "free_preview_enabled": True
    }

def get_first_matching_pdf(query):
    q = _clean_display_text(query).strip().lower()

    if not q:
        return ""

    matches = []

    for book in catalog_books:
        title_low = book.get("title_low", "")
        search_text = book.get("search_text", "")
        link = book.get("link", "")

        if not link:
            continue

        score = 0

        if q in title_low:
            score += 100

        if q in search_text:
            score += 25

        tokens = [t for t in re.findall(r"[a-zA-Z0-9']+", q) if len(t) > 1]
        for tok in tokens:
            if tok in title_low:
                score += 10
            if tok in search_text:
                score += 3

        if score > 0:
            matches.append((score, link))

    if not matches:
        return ""

    matches.sort(key=lambda x: x[0], reverse=True)
    return matches[0][1]

def select_pdf_from_query(query):
    pdf_url = get_first_matching_pdf(query)
    return pdf_url

# =========================================================
# FEATURED 3-COVER SHELF
# =========================================================

def build_featured_shelf_html():
    if not catalog_books:
        return """
        <div class="featured_wrap">
          <div class="featured_row">
            <div class="featured_empty">No featured books available.</div>
          </div>
        </div>
        """

    picks = random.sample(catalog_books, min(3, len(catalog_books)))

    cards = []
    for book in picks:
        title = html.escape(book["title"])
        thumb = html.escape(book["thumb"], quote=True) if book["thumb"] else ""

        if thumb:
            card_html = f"""
            <div class="featured_card featured_card_locked">
              <div class="featured_cover">
                <img src="{thumb}" alt="{title}" class="featured_img" loading="lazy" referrerpolicy="no-referrer" onerror="this.style.display='none'; this.parentNode.classList.add('featured_cover_fallback_active'); this.parentNode.innerHTML='<div class=&quot;featured_fallback_text&quot;>' + this.alt + '</div>';">
              </div>
              <div class="featured_title">{title}</div>
            </div>
            """
        else:
            card_html = f"""
            <div class="featured_card featured_card_locked">
              <div class="featured_cover featured_cover_fallback">
                <div class="featured_fallback_text">{title}</div>
              </div>
              <div class="featured_title">{title}</div>
            </div>
            """

        cards.append(card_html)

    return f"""
    <div class="featured_wrap">
      <div class="featured_row">
        {''.join(cards)}
      </div>
    </div>
    """

# =========================================================
# BROWSE MATRIX VAULT
# =========================================================

def build_selector_choices(matches):
    if not matches:
        return gr.update(choices=[], value=None)

    choices = []
    for book in matches:
        title = str(book.get("title", "")).strip()
        if title:
            choices.append(title)

    return gr.update(choices=choices, value=None)


def browse_matrix_category(category_name):
    q = _clean_display_text(category_name).strip()

    if not q:
        return gr.update(choices=[], value=None)

    category_terms = None
    for label, terms in CATEGORY_GROUPS:
        if label.lower() == q.lower():
            category_terms = terms
            break

    if category_terms is None:
        category_terms = [q.lower()]

    matches = [
        b for b in catalog_books
        if _book_matches_category(b, category_terms)
    ]

    matches = sorted(matches, key=lambda b: b["title_low"])[:200]

    return build_selector_choices(matches)


def browse_matrix_vault(query):
    q = str(query or "").strip()
    q_low = q.lower()

    if not q_low:
        return gr.update(choices=[], value=None)

    if len(q_low) == 1 and q_low.isalpha():
        matches = [
            b for b in sorted(catalog_books, key=lambda b: b["title_low"])
            if b["title_low"].startswith(q_low)
        ][:200]
    else:
        q_tokens = [t for t in re.findall(r"[a-zA-Z0-9']+", q_low) if len(t) > 1]

        ranked = []
        for book in catalog_books:
            title_low = book["title_low"]
            hay = book["search_text"]
            score = 0

            if q_low in title_low:
                score += 200
            if q_low in hay:
                score += 80

            for tok in q_tokens:
                if re.search(rf"\b{re.escape(tok)}\b", title_low):
                    score += 30
                elif tok in title_low:
                    score += 12

                if re.search(rf"\b{re.escape(tok)}\b", hay):
                    score += 10
                elif tok in hay:
                    score += 4

            if score > 0:
                ranked.append((score, book))

        ranked.sort(key=lambda x: (-x[0], x[1]["title_low"]))
        matches = [book for _, book in ranked[:200]]

    return build_selector_choices(matches)


def handle_book_open(selected_book, usage, membership, request: gr.Request):
    result = resolve_book_access(selected_book, usage, membership, request)

    usage = result["usage"]
    viewer_url = result["viewer_url"]
    access_html = result["access_html"]
    cta_html = result["cta_html"]

    viewer_html = build_pdf_viewer_html(viewer_url) if viewer_url else ""

    final_access_html = access_html + cta_html

    return final_access_html, usage, viewer_html

def handle_book_open_with_continue(selected_book, usage, membership, user_session, request: gr.Request):
    access_html, usage, viewer_html = handle_book_open(selected_book, usage, membership, request)
    continue_reading_html = save_continue_reading(selected_book, user_session)
    return access_html, usage, viewer_html, continue_reading_html

# =========================================================
# LOAD FINAL JEWEL DATA
# =========================================================
jewel_df = load_jewel_df()
print("JEWELS LOADED:", len(jewel_df))
vault_state = make_jewel_state(jewel_df)
initial_daily_html, vault_state = refresh_jewel(vault_state)
initial_featured_html = build_featured_shelf_html()
promo_state = make_promo_state()
initial_promo_html, promo_state = rotate_promo(promo_state)
usage_state = make_usage_state()

def refresh_vault_panels(state):
    daily_html, new_state = refresh_jewel(state)
    featured_html = build_featured_shelf_html()
    return daily_html, featured_html, new_state

def enable_premium(membership):
    membership = normalize_membership_state(membership)
    membership["is_premium"] = True
    membership["premium_tier"] = "premium"
    membership["subscription_status"] = "active"
    return membership, membership, build_membership_status_html(membership)

def disable_premium(membership):
    membership = normalize_membership_state(membership)
    membership["is_premium"] = False
    membership["premium_tier"] = "free"
    membership["subscription_status"] = "inactive"
    return membership, membership, build_membership_status_html(membership)

def sign_up_member(email, password, confirm_password, user_session, membership):
    user_session = normalize_user_session_state(user_session)
    membership = normalize_membership_state(membership)

    email = normalize_email(email)
    password = str(password or "")
    confirm_password = str(confirm_password or "")

    if not email or "@" not in email or "." not in email:
        result_html = build_restore_result_html(
            "Sign Up Failed",
            "Enter a valid email address."
        )
        return user_session, user_session, build_account_status_html(user_session, membership), result_html

    if len(password) < 6:
        result_html = build_restore_result_html(
            "Sign Up Failed",
            "Password must be at least 6 characters."
        )
        return user_session, user_session, build_account_status_html(user_session, membership), result_html

    if password != confirm_password:
        result_html = build_restore_result_html(
            "Sign Up Failed",
            "Password and confirm password do not match."
        )
        return user_session, user_session, build_account_status_html(user_session, membership), result_html

    existing = find_user_by_email(email)
    if existing:
        result_html = build_restore_result_html(
            "Sign Up Failed",
            "An account already exists for that email. Use Log In instead."
        )
        return user_session, user_session, build_account_status_html(user_session, membership), result_html

    data = load_user_db()
    user = make_user_record(email, password)
    data["users"].append(user)
    save_user_db(data)

    user_session = user_record_to_session(user)

    result_html = build_restore_result_html(
        "Account Created",
        "Your member account has been created and you are now signed in.",
        extra_lines=[
            f"Email: <b>{html.escape(user_session['email'])}</b>",
            f"Tier: <b>{html.escape(user_session['tier'].title())}</b>"
        ]
    )

    return user_session, user_session, build_account_status_html(user_session, membership), result_html


def log_in_member(email, password, user_session, membership):
    user_session = normalize_user_session_state(user_session)
    membership = normalize_membership_state(membership)

    email = normalize_email(email)
    password = str(password or "")

    if not email or not password:
        result_html = build_restore_result_html(
            "Log In Failed",
            "Enter your email and password."
        )
        return user_session, user_session, build_account_status_html(user_session, membership), result_html

    user = find_user_by_email(email)
    if not user:
        result_html = build_restore_result_html(
            "Log In Failed",
            "No account was found for that email."
        )
        return user_session, user_session, build_account_status_html(user_session, membership), result_html

    if not str(user.get("password_hash", "")).strip():
        result_html = build_restore_result_html(
            "Log In Failed",
            "No password has been set for this account yet. Restore Premium first, then use Set Password."
        )
        return user_session, user_session, build_account_status_html(user_session, membership), result_html

    if str(user.get("password_hash", "")).strip() != _hash_password(password):
        result_html = build_restore_result_html(
            "Log In Failed",
            "Incorrect password."
        )
        return user_session, user_session, build_account_status_html(user_session, membership), result_html

    user_session = user_record_to_session(user)

    result_html = build_restore_result_html(
        "Logged In",
        "You are now signed in.",
        extra_lines=[
            f"Email: <b>{html.escape(user_session['email'])}</b>",
            f"Tier: <b>{html.escape(user_session['tier'].title())}</b>"
        ]
    )

    return user_session, user_session, build_account_status_html(user_session, membership), result_html


def claim_restored_account(email, password, confirm_password, user_session, membership):
    user_session = normalize_user_session_state(user_session)
    membership = normalize_membership_state(membership)

    email = normalize_email(email)
    password = str(password or "")
    confirm_password = str(confirm_password or "")

    if not email:
        result_html = build_restore_result_html(
            "Set Password Failed",
            "Enter your email address first."
        )
        return user_session, user_session, build_account_status_html(user_session, membership), result_html

    if len(password) < 6:
        result_html = build_restore_result_html(
            "Set Password Failed",
            "Password must be at least 6 characters."
        )
        return user_session, user_session, build_account_status_html(user_session, membership), result_html

    if password != confirm_password:
        result_html = build_restore_result_html(
            "Set Password Failed",
            "Password and confirm password do not match."
        )
        return user_session, user_session, build_account_status_html(user_session, membership), result_html

    user = find_user_by_email(email)
    if not user:
        result_html = build_restore_result_html(
            "Set Password Failed",
            "No restored account exists for that email yet. Restore Premium first."
        )
        return user_session, user_session, build_account_status_html(user_session, membership), result_html

    stored_email = normalize_email(membership.get("restored_via_email", ""))
    user_email = normalize_email(user.get("email", ""))

    if stored_email and email != stored_email and email != user_email:
        result_html = build_restore_result_html(
            "Set Password Failed",
            "That email does not match the restored premium account."
        )
        return user_session, user_session, build_account_status_html(user_session, membership), result_html

    data = load_user_db()
    idx = find_user_index_by_email(data, email)

    if idx < 0:
        result_html = build_restore_result_html(
            "Set Password Failed",
            "No local account record was found for that email."
        )
        return user_session, user_session, build_account_status_html(user_session, membership), result_html

    data["users"][idx]["password_hash"] = _hash_password(password)
    data["users"][idx]["tier"] = "premium" if membership.get("is_premium") else data["users"][idx].get("tier", "free")
    data["users"][idx]["stripe_customer_id"] = str(membership.get("customer_id", "")).strip()
    data["users"][idx]["stripe_subscription_id"] = str(membership.get("subscription_id", "")).strip()
    data["users"][idx]["subscription_status"] = str(membership.get("subscription_status", "inactive")).strip()
    data["users"][idx]["updated_at"] = datetime.utcnow().isoformat()

    save_user_db(data)

    user_session = user_record_to_session(data["users"][idx])

    result_html = build_restore_result_html(
        "Password Set",
        "Your account password has been created. You can now log in normally with this email and password.",
        extra_lines=[
            f"Email: <b>{html.escape(user_session['email'])}</b>",
            f"Tier: <b>{html.escape(user_session['tier'].title())}</b>"
        ]
    )

    return user_session, user_session, build_account_status_html(user_session, membership), result_html


def log_out_member(user_session, membership):
    membership = normalize_membership_state(membership)
    user_session = make_user_session_state()

    result_html = build_restore_result_html(
        "Logged Out",
        "You have been signed out of your member account."
    )

    return user_session, user_session, build_account_status_html(user_session, membership), result_html

def set_restored_premium_password(email, password, confirm_password, user_session, membership, restore_claim_state):
    user_session = normalize_user_session_state(user_session)
    membership = normalize_membership_state(membership)
    restore_claim_state = normalize_restore_claim_state(restore_claim_state)

    email = normalize_email(email)
    password = str(password or "")
    confirm_password = str(confirm_password or "")

    if not email or "@" not in email or "." not in email:
        result_html = build_restore_result_html(
            "Set Password Failed",
            "Enter a valid premium account email."
        )
        return (
            user_session,
            user_session,
            build_account_status_html(user_session, membership),
            result_html,
            restore_claim_state,
            build_restore_password_claim_html(restore_claim_state)
        )

    if len(password) < 6:
        result_html = build_restore_result_html(
            "Set Password Failed",
            "Password must be at least 6 characters."
        )
        return (
            user_session,
            user_session,
            build_account_status_html(user_session, membership),
            result_html,
            restore_claim_state,
            build_restore_password_claim_html(restore_claim_state)
        )

    if password != confirm_password:
        result_html = build_restore_result_html(
            "Set Password Failed",
            "Password and confirm password do not match."
        )
        return (
            user_session,
            user_session,
            build_account_status_html(user_session, membership),
            result_html,
            restore_claim_state,
            build_restore_password_claim_html(restore_claim_state)
        )

    data = load_user_db()
    idx = find_user_index_by_email(data, email)

    if idx < 0:
        result_html = build_restore_result_html(
            "Set Password Failed",
            "No local premium account record was found for that email. Restore premium first."
        )
        return (
            user_session,
            user_session,
            build_account_status_html(user_session, membership),
            result_html,
            restore_claim_state,
            build_restore_password_claim_html(restore_claim_state)
        )

    data["users"][idx]["password_hash"] = _hash_password(password)
    data["users"][idx]["updated_at"] = datetime.utcnow().isoformat()

    if membership.get("is_premium"):
        data["users"][idx]["tier"] = "premium"
        data["users"][idx]["subscription_status"] = membership.get("subscription_status", "active")
        data["users"][idx]["stripe_customer_id"] = membership.get("customer_id", "")
        data["users"][idx]["stripe_subscription_id"] = membership.get("subscription_id", "")

    save_user_db(data)

    updated_user = data["users"][idx]
    user_session = user_record_to_session(updated_user)

    restore_claim_state = {
        "email": email,
        "ready_for_password_claim": False
    }

    result_html = build_restore_result_html(
        "Password Set",
        "Your premium account password has been created. You are now signed in.",
        extra_lines=[
            f"Email: <b>{html.escape(user_session['email'])}</b>",
            f"Tier: <b>{html.escape(user_session['tier'].title())}</b>"
        ]
    )

    return (
        user_session,
        user_session,
        build_account_status_html(user_session, membership),
        result_html,
        restore_claim_state,
        build_restore_password_claim_html(restore_claim_state)
    )

def restore_account_status_on_load(user_session, membership):
    user_session = normalize_user_session_state(user_session)
    membership = normalize_membership_state(membership)
    continue_reading_html = build_continue_reading_html(user_session)
    return user_session, build_account_status_html(user_session, membership), user_session, continue_reading_html


def reset_daily_opens(usage):
    usage = normalize_usage_state(usage)
    usage["opens_today"] = 0
    access_html = build_access_status_html(usage)
    return usage, access_html

def auto_restore_from_local(membership):
    membership = normalize_membership_state(membership)

    if membership.get("is_premium"):
        return membership, membership, build_membership_status_html(membership)

    stored_email = str(membership.get("restored_via_email", "")).strip().lower()

    if stored_email:
        try:
            restored_membership, debug_membership, membership_html, _, _ = restore_premium_access(
                stored_email,
                membership,
                make_user_session_state()
            )
            return restored_membership, debug_membership, membership_html
        except:
            pass

    return membership, membership, build_membership_status_html(membership)


def render_stripe_flash_on_load(flash):
    flash, html_out = consume_stripe_flash(flash)
    return flash, html_out

def create_customer_portal_html(membership, user_session):
    membership = normalize_membership_state(membership)
    user_session = normalize_user_session_state(user_session)

    customer_id = str(membership.get("customer_id", "")).strip()

    if not customer_id and user_session.get("logged_in"):
        data, idx, user = _get_user_record_from_session(user_session)
        if user:
            customer_id = str(user.get("stripe_customer_id", "")).strip()

    if not customer_id:
        return gr.update(
            visible=True,
            value="""
            <div class="results_wrap">
              <div class="card">
                <div class="card_title">Manage Subscription</div>
                <div class="body_text">
                  No Stripe billing account was found for this user yet.
                </div>
              </div>
            </div>
            """
        )

    try:
        return_url = str(APP_BASE_URL or "").strip().rstrip("/")
        if not return_url:
            return_url = "https://vault.urbaninteractiveadventures.com"

        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=return_url
        )

        portal_url = html.escape(str(session.url or "").strip(), quote=True)

        if not portal_url:
            return gr.update(
                visible=True,
                value="""
                <div class="results_wrap">
                  <div class="card">
                    <div class="card_title">Manage Subscription</div>
                    <div class="body_text">
                      The billing portal could not be opened right now.
                    </div>
                  </div>
                </div>
                """
            )

        return gr.update(
            visible=True,
            value=f"""
            <div class="results_wrap">
              <div class="card">
                <div class="card_title">Manage Subscription</div>
                <div class="body_text">
                  Update billing details, manage payment methods, or cancel your subscription.
                </div>
                <div style="margin-top:14px;">
                  <a href="{portal_url}" target="_self" rel="noopener noreferrer" class="portal_btn">
                    Open Stripe Billing Portal
                  </a>
                </div>
              </div>
            </div>
            """
        )

    except Exception as e:
        return gr.update(
            visible=True,
            value=f"""
            <div class="results_wrap">
              <div class="card">
                <div class="card_title">Manage Subscription</div>
                <div class="body_text">
                  The billing portal could not be opened right now.
                </div>
                <div class="small_text" style="margin-top:8px;">
                  {html.escape(str(e))}
                </div>
              </div>
            </div>
            """
        )

def make_restore_claim_state():
    return {
        "email": "",
        "ready_for_password_claim": False
    }

def normalize_restore_claim_state(state):
    if not state or not isinstance(state, dict):
        state = make_restore_claim_state()

    email = normalize_email(state.get("email", ""))
    ready = bool(state.get("ready_for_password_claim", False))

    return {
        "email": email,
        "ready_for_password_claim": ready
    }

def build_restore_password_claim_html(state):
    state = normalize_restore_claim_state(state)

    if not state["ready_for_password_claim"] or not state["email"]:
        return ""

    return f"""
    <div class="results_wrap">
      <div class="card">
        <div class="card_title">Set Premium Password</div>
        <div class="body_text">
          Premium has been restored for <b>{html.escape(state["email"])}</b>.
          Set your password now so you can log in normally next time.
        </div>
      </div>
    </div>
    """

def make_utility_panel_state():
    return {
        "open_panel": ""
    }

def normalize_utility_panel_state(panel_state):
    if not panel_state or not isinstance(panel_state, dict):
        panel_state = make_utility_panel_state()

    open_panel = str(panel_state.get("open_panel", "")).strip().lower()

    if open_panel not in {"", "membership", "favorites", "restore"}:
        open_panel = ""

    return {
        "open_panel": open_panel
    }

def _panel_visibility_updates(open_panel):
    return (
        gr.update(visible=(open_panel == "membership")),
        gr.update(visible=(open_panel == "favorites")),
        gr.update(visible=(open_panel == "restore"))
    )

def toggle_membership_panel(panel_state):
    panel_state = normalize_utility_panel_state(panel_state)

    if panel_state["open_panel"] == "membership":
        panel_state["open_panel"] = ""
    else:
        panel_state["open_panel"] = "membership"

    membership_update, favorites_update, restore_update = _panel_visibility_updates(panel_state["open_panel"])
    return membership_update, favorites_update, restore_update, panel_state

def toggle_favorites_panel(panel_state):
    panel_state = normalize_utility_panel_state(panel_state)

    if panel_state["open_panel"] == "favorites":
        panel_state["open_panel"] = ""
    else:
        panel_state["open_panel"] = "favorites"

    membership_update, favorites_update, restore_update = _panel_visibility_updates(panel_state["open_panel"])
    return membership_update, favorites_update, restore_update, panel_state

def toggle_restore_panel(panel_state):
    panel_state = normalize_utility_panel_state(panel_state)

    if panel_state["open_panel"] == "restore":
        panel_state["open_panel"] = ""
    else:
        panel_state["open_panel"] = "restore"

    membership_update, favorites_update, restore_update = _panel_visibility_updates(panel_state["open_panel"])
    return membership_update, favorites_update, restore_update, panel_state


def make_category_panel_state():
    return {
        "open_category": ""
    }

def normalize_category_panel_state(state):
    if not state or not isinstance(state, dict):
        state = make_category_panel_state()

    return {
        "open_category": str(state.get("open_category", "")).strip()
    }

def toggle_category_results(category_name, panel_state):
    panel_state = normalize_category_panel_state(panel_state)
    category_name = str(category_name or "").strip()

    if panel_state["open_category"].lower() == category_name.lower():
        panel_state["open_category"] = ""
        return gr.update(choices=[], value=None), panel_state

    panel_state["open_category"] = category_name
    selector_update = browse_matrix_category(category_name)
    return selector_update, panel_state

# =========================================================
# MOBILE-FIRST APP CSS
# =========================================================
CUSTOM_HEAD = """
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover">
"""
CUSTOM_CSS = """
:root {
  --bg-1: #020617;
  --bg-2: #0f172a;
  --card: rgba(15,23,42,0.96);
  --card-2: rgba(30,41,59,0.94);
  --line: rgba(148,163,184,0.16);
  --text: #f8fafc;
  --muted: #cbd5e1;
  --soft: #94a3b8;

  --browse: #0f766e;
  --browse-hover: #0d5f59;

  --refresh: #7c3aed;
  --refresh-hover: #6d28d9;
}

html, body, .gradio-container {
  margin: 0 !important;
  padding: 0 !important;
  width: 100% !important;
  max-width: 100% !important;
  overflow-x: hidden !important;
  background: radial-gradient(circle at top, #0f172a 0%, #020617 45%, #000000 100%) !important;
  color: var(--text) !important;
  font-family: Inter, Arial, sans-serif !important;
}

.gradio-container {
  width: 100% !important;
  max-width: 100% !important;
  overflow-x: hidden !important;
}

#app_shell {
  width: 100% !important;
  max-width: 760px !important;
  margin: 0 auto !important;
  padding: 14px !important;
  box-sizing: border-box !important;
  overflow-x: hidden !important;
}

#app_shell * {
  max-width: 100%;
  box-sizing: border-box;
}

.title_wrap {
  border: 1px solid rgba(148,163,184,0.16);
  border-radius: 18px;
  background: linear-gradient(135deg, rgba(37,99,235,0.14), rgba(15,23,42,0.96));
  padding: 18px 14px 16px 14px;
  margin-bottom: 8px;
  text-align: center;
  box-shadow: 0 10px 24px rgba(0,0,0,0.24);
}

.title_main {
  font-size: 30px;
  font-weight: 900;
  line-height: 1.05;
  margin-bottom: 8px;
  color: #ffffff;
}

.title_tag {
  font-size: 13px;
  line-height: 1.5;
  color: var(--muted);
  max-width: 100%;
  margin: 0 auto;
}

.top_utility_wrap {
  width: 100%;
  margin: 0 0 14px 0;
}

.top_utility_row {
  display: flex;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  justify-content: center;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
  flex-wrap: nowrap;
}

.top_utility_btn,
.top_utility_btn button {
  background: rgba(30,41,59,0.94) !important;
  color: #ffffff !important;
  border: 1px solid rgba(148,163,184,0.16) !important;
  border-radius: 12px !important;
  font-weight: 800 !important;
  min-height: 42px !important;
  padding: 0 16px !important;
  width: auto !important;
  min-width: 0 !important;
  flex: 0 0 auto !important;
  box-shadow: 0 6px 14px rgba(0,0,0,0.18) !important;
}

.top_utility_btn:hover,
.top_utility_btn button:hover {
  background: rgba(51,65,85,0.98) !important;
  color: #ffffff !important;
}

.utility_panel_wrap {
  width: 100%;
  margin-bottom: 14px;
}

.utility_panel_wrap .wrap,
.utility_panel_wrap .block,
.utility_panel_wrap fieldset {
  border: 1px solid rgba(148,163,184,0.16) !important;
  border-radius: 16px !important;
  background: rgba(15,23,42,0.96) !important;
  padding: 12px !important;
}

.utility_panel_title {
  font-size: 15px;
  font-weight: 800;
  color: #ffffff;
  margin: 0 0 10px 2px;
}

@media (max-width: 767px) {
  .top_utility_row {
    gap: 6px !important;
    justify-content: center !important;
    align-items: center !important;
    flex-wrap: nowrap !important;
  }

  .top_utility_btn,
  .top_utility_btn button {
    min-height: 38px !important;
    font-size: 12px !important;
    padding: 0 12px !important;
    width: auto !important;
    min-width: 0 !important;
    flex: 0 0 auto !important;
    white-space: nowrap !important;
  }
}

.top_utility_wrap {
  width: 100%;
  margin: 0 0 12px 0;
}

.top_utility_btn,
.top_utility_btn button {
  background: rgba(30,41,59,0.94) !important;
  color: #ffffff !important;
  border: 1px solid rgba(148,163,184,0.16) !important;
  border-radius: 12px !important;
  font-weight: 800 !important;
  min-height: 42px !important;
  box-shadow: 0 6px 14px rgba(0,0,0,0.18) !important;
}

.top_utility_btn:hover,
.top_utility_btn button:hover {
  background: rgba(51,65,85,0.98) !important;
  color: #ffffff !important;
}

.utility_panel_wrap {
  width: 100%;
  margin: 0 0 12px 0;
}

.utility_panel_wrap > .gradio-column,
.utility_panel_wrap .wrap,
.utility_panel_wrap .block,
.utility_panel_wrap fieldset {
  border: 1px solid rgba(148,163,184,0.16) !important;
  border-radius: 16px !important;
  background: rgba(15,23,42,0.96) !important;
  padding: 12px !important;
}

.utility_panel_title {
  font-size: 15px;
  font-weight: 800;
  color: #ffffff;
  margin: 0 0 10px 2px;
}

/* === Membership / Utility Input Styling Patch === */

.utility_panel_wrap input,
.utility_panel_wrap textarea {
  background: rgba(15,23,42,0.82) !important;
  color: #ffffff !important;
  border: 1px solid rgba(148,163,184,0.18) !important;
  border-radius: 12px !important;
}

.utility_panel_wrap label {
  color: #e2e8f0 !important;
  font-weight: 700 !important;
}

.membership_section_card {
  border: 1px solid rgba(148,163,184,0.14);
  border-radius: 16px;
  background: rgba(30,41,59,0.55);
  padding: 12px;
  margin-top: 10px;
}

.membership_section_heading {
  font-size: 14px;
  font-weight: 800;
  color: #ffffff;
  margin: 0 0 10px 2px;
  letter-spacing: 0.02em;
}

.membership_primary_btn,
.membership_primary_btn button {
  background: #dc2626 !important;
  color: #ffffff !important;
  border: none !important;
  border-radius: 12px !important;
  font-weight: 800 !important;
}

.membership_primary_btn:hover,
.membership_primary_btn button:hover {
  background: #b91c1c !important;
}

.membership_secondary_btn,
.membership_secondary_btn button {
  background: rgba(51,65,85,0.95) !important;
  color: #ffffff !important;
  border: 1px solid rgba(148,163,184,0.18) !important;
  border-radius: 12px !important;
  font-weight: 800 !important;
}

.membership_secondary_btn:hover,
.membership_secondary_btn button:hover {
  background: rgba(71,85,105,0.98) !important;
}

@media (max-width: 767px) {
  .top_utility_row {
    gap: 6px;
  }

  .top_utility_btn,
  .top_utility_btn button {
    min-height: 40px !important;
    font-size: 13px !important;
  }
}

/* ================================
  PRIMARY ACTION BUTTON STYLE
  (Universal Red CTA)
  ================================ */

.utility_panel_wrap button {
  background: linear-gradient(135deg, #ff1e1e, #d10000) !important;
  color: #ffffff !important;
  border: none !important;
  font-weight: 800 !important;
  border-radius: 12px !important;
  box-shadow: 0 8px 18px rgba(0,0,0,0.25) !important;
}

.utility_panel_wrap button:hover {
  background: linear-gradient(135deg, #ff3b3b, #ff1e1e) !important;
  transform: translateY(-1px);
}

#query_box {
  width: 100% !important;
  margin-bottom: 14px !important;
}

#query_box > div,
#query_box .wrap,
#query_box .block,
#query_box .gr-box,
#query_box .gr-input,
#query_box textarea {
  width: 100% !important;
}

#query_box textarea {
  display: block !important;
  min-height: 82px !important;
  height: 82px !important;
  max-height: 120px !important;
  visibility: visible !important;
  opacity: 1 !important;
  resize: vertical !important;
  overflow-y: auto !important;
  background: rgba(30,41,59,1) !important;
  color: #ffffff !important;
  border: 1px solid rgba(148,163,184,0.24) !important;
  border-radius: 14px !important;
  font-size: 16px !important;
  line-height: 1.45 !important;
  padding: 14px 16px !important;
  box-sizing: border-box !important;
}

#query_box textarea::placeholder {
  color: #cbd5e1 !important;
  opacity: 1 !important;
}

#browse_btn, #browse_btn button {
  background: var(--browse) !important;
  color: #ffffff !important;
  border: none !important;
  border-radius: 12px !important;
  font-weight: 800 !important;
}
#browse_btn:hover, #browse_btn button:hover {
  background: var(--browse-hover) !important;
}

#continue_reading_btn, #continue_reading_btn button {
  background: #dc2626 !important;
  color: #ffffff !important;
  border: none !important;
  border-radius: 12px !important;
  font-weight: 800 !important;
  margin-top: 8px !important;
}

#continue_reading_btn:hover, #continue_reading_btn button:hover {
  background: #b91c1c !important;
}

#refresh_btn, #refresh_btn button {
  background: var(--refresh) !important;
  color: #ffffff !important;
  border: none !important;
  border-radius: 12px !important;
  font-weight: 800 !important;
}
#refresh_btn:hover, #refresh_btn button:hover {
  background: var(--refresh-hover) !important;
}

#open_pdf_btn, #open_pdf_btn button {
  background: #dc2626 !important;
  color: #ffffff !important;
  border: none !important;
  border-radius: 12px !important;
  font-weight: 800 !important;
  margin-bottom: 10px !important;
}
#open_pdf_btn:hover, #open_pdf_btn button:hover {
  background: #b91c1c !important;
}

#result_selector_radio {
  margin: 14px 0 14px 0 !important;
}

#result_selector_radio .wrap,
#result_selector_radio .block,
#result_selector_radio fieldset {
  border: 1px solid rgba(148,163,184,0.16) !important;
  border-radius: 16px !important;
  background: rgba(15,23,42,0.96) !important;
  padding: 12px !important;
  max-height: 420px !important;
  overflow-y: auto !important;
}

#result_selector_radio label {
  background: rgba(30,41,59,0.94) !important;
  border: 1px solid rgba(148,163,184,0.14) !important;
  border-radius: 12px !important;
  padding: 12px 14px !important;
  margin-bottom: 8px !important;
  color: #ffffff !important;
  font-weight: 700 !important;
  line-height: 1.35 !important;
}

#result_selector_radio label[data-selected="true"] {
  background: rgba(185,28,28,0.92) !important;
  border: 1px solid rgba(239,68,68,0.5) !important;
  color: #ffffff !important;
}

#result_selector_radio input[type="radio"] {
  accent-color: #dc2626 !important;
}

#favorites_selector_radio {
  margin: 10px 0 10px 0 !important;
}

#favorites_selector_radio .wrap,
#favorites_selector_radio .block,
#favorites_selector_radio fieldset {
  border: 1px solid rgba(148,163,184,0.16) !important;
  border-radius: 16px !important;
  background: rgba(15,23,42,0.96) !important;
  padding: 12px !important;
}

#favorites_selector_radio label {
  background: rgba(30,41,59,0.94) !important;
  border: 1px solid rgba(148,163,184,0.14) !important;
  border-radius: 12px !important;
  padding: 12px 14px !important;
  margin-bottom: 8px !important;
  color: #ffffff !important;
  font-weight: 700 !important;
  line-height: 1.35 !important;
  transition: background 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease !important;
}

#favorites_selector_radio label[data-selected="true"],
#favorites_selector_radio label:has(input[type="radio"]:checked) {
  background: rgba(185,28,28,0.92) !important;
  border: 1px solid rgba(239,68,68,0.75) !important;
  color: #ffffff !important;
  box-shadow: 0 0 0 1px rgba(239,68,68,0.35) inset !important;
}

#favorites_selector_radio input[type="radio"] {
  accent-color: #dc2626 !important;
}

@media (max-width: 767px) {
  #result_selector_radio .wrap,
  #result_selector_radio .block,
  #result_selector_radio fieldset {
    max-height: 360px !important;
    padding: 10px !important;
  }

  #result_selector_radio label {
    padding: 10px 12px !important;
    font-size: 15px !important;
  }
}

.featured_wrap {
  width: 100%;
  margin: 0 0 12px 0;
}

.featured_row {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.featured_card {
  text-decoration: none !important;
  display: block;
}

.featured_card_locked {
  cursor: default;
}

.featured_cover {
  width: 100%;
  aspect-ratio: 0.72 / 1;
  border-radius: 14px;
  overflow: hidden;
  border: 1px solid rgba(148,163,184,0.18);
  background: rgba(30,41,59,0.94);
  box-shadow: 0 10px 24px rgba(0,0,0,0.22);
  display: flex;
  align-items: center;
  justify-content: center;
}

.featured_img {
  width: 100%;
  height: 100%;
  object-fit: contain;
  display: block;
  background: rgba(30,41,59,0.94);
  image-rendering: auto;
}

.featured_cover_fallback_active {
  display: flex !important;
  align-items: center !important;
  justify-content: center !important;
  padding: 10px !important;
  text-align: center !important;
  background: linear-gradient(180deg, rgba(37,99,235,0.18), rgba(30,41,59,0.96)) !important;
}

.featured_fallback_text {
  font-size: 13px;
  line-height: 1.35;
  font-weight: 700;
  color: #ffffff;
  display: -webkit-box;
  -webkit-line-clamp: 5;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.featured_title {
  margin-top: 6px;
  font-size: 12px;
  line-height: 1.35;
  color: #e2e8f0;
  text-align: center;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  min-height: 32px;
}

.featured_empty {
  width: 100%;
  text-align: center;
  color: var(--soft);
  font-size: 13px;
  padding: 10px 0;
}

.featured_cover_fallback_active {
  display: flex !important;
  align-items: center !important;
  justify-content: center !important;
  padding: 10px !important;
  text-align: center !important;
  background: linear-gradient(180deg, rgba(37,99,235,0.18), rgba(30,41,59,0.96)) !important;
}

.results_wrap, .side_wrap {
  border: 1px solid rgba(148,163,184,0.16);
  border-radius: 16px;
  background: rgba(15,23,42,0.96);
  padding: 10px;
  box-shadow: 0 10px 24px rgba(0,0,0,0.22);
  margin-bottom: 12px;
}

.card {
  border: 1px solid rgba(148,163,184,0.14);
  border-radius: 14px;
  background: rgba(30,41,59,0.94);
  padding: 12px;
  margin-bottom: 10px;
}

.card_title {
  font-size: 18px;
  font-weight: 800;
  margin-bottom: 8px;
  color: #ffffff;
}

.body_text {
  color: #ffffff;
  font-size: 14px;
  line-height: 1.65;
  white-space: pre-wrap;
}

.small_text {
  color: var(--soft);
  font-size: 12px;
  line-height: 1.45;
}

.daily_jewel_quote_wrap {
  text-align: center;
  margin: 4px 0 8px 0;
}

.daily_jewel_quote {
  font-size: 16px;
  line-height: 1.55;
  color: #ffffff;
  text-align: center;
  white-space: normal;
  margin: 0;
}

.daily_jewel_quote_inline_mark {
  color: #cbd5e1;
  opacity: 0.95;
  font-size: 18px;
  font-weight: 700;
}

.daily_jewel_meta {
  text-align: center;
  margin-top: 6px;
}

.daily_jewel_author {
  font-size: 15px;
  font-weight: 800;
  color: #ffffff;
  margin-bottom: 2px;
}

.daily_jewel_book {
  font-size: 14px;
  color: #cbd5e1;
}

.results_wrap a:hover,
.featured_wrap a:hover {
  text-decoration: underline !important;
}

.promo_shell {
  width: 100%;
  margin: 12px 0 12px 0;
}

.promo_card {
  padding: 12px;
}

.promo_link {
  display: block;
  text-decoration: none !important;
}

.promo_stage {
  width: 100%;
  aspect-ratio: 1.55 / 1;
  min-height: 0;
  max-height: 520px;
  border-radius: 14px;
  overflow: hidden;
  border: 1px solid rgba(148,163,184,0.16);
  background: rgba(30,41,59,0.94);
  box-shadow: 0 10px 24px rgba(0,0,0,0.22);
  display: flex;
  align-items: center;
  justify-content: center;
}

.promo_img {
  width: 100%;
  height: 100%;
  object-fit: contain;
  object-position: center;
  display: block;
  background: rgba(30,41,59,0.94);
}

.promo_fallback {
  width: 100%;
  min-height: 220px;
  display: flex;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 18px;
  color: #ffffff;
  font-size: 20px;
  font-weight: 800;
  line-height: 1.3;
  background: linear-gradient(180deg, rgba(37,99,235,0.18), rgba(30,41,59,0.96));
}

.promo_stage_empty {
  min-height: 140px;
}

.promo_empty_text {
  color: #cbd5e1;
  font-size: 16px;
  font-weight: 700;
  text-align: center;
  padding: 20px;
}

.promo_meta {
  padding-top: 10px;
  text-align: center;
}

.promo_name {
  color: #ffffff;
  font-size: 15px;
  font-weight: 800;
  line-height: 1.35;
}

@media (max-width: 767px) {
  .promo_stage {
    aspect-ratio: 1.55 / 1;
    max-height: 320px;
  }

  .promo_fallback {
    min-height: 180px;
    font-size: 18px;
    padding: 14px;
  }

  .promo_name {
    font-size: 14px;
  }
}

.category_wrap {
  width: 100%;
  margin: 0 0 12px 0;
}

.category_label {
  font-size: 14px;
  font-weight: 800;
  color: #ffffff;
  margin: 0 0 8px 2px;
}

.category_row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.category_pill {
  background: rgba(30,41,59,0.94);
  color: #e2e8f0;
  border: 1px solid rgba(148,163,184,0.16);
  border-radius: 999px;
  padding: 8px 12px;
  font-size: 12px;
  font-weight: 700;
  line-height: 1;
  box-shadow: 0 6px 14px rgba(0,0,0,0.18);
}

.category_empty {
  color: var(--soft);
  font-size: 13px;
  padding: 4px 0;
}

.category_section_label {
  font-size: 16px;
  font-weight: 800;
  color: #ffffff;
  margin: 6px 0 10px 0;
  text-align: center !important;
  width: 100% !important;
}

#category_button_stack {
  display: flex !important;
  flex-direction: column !important;
  gap: 6px !important;
  margin-bottom: 12px !important;
}

#category_button_row {
  display: flex !important;
  gap: 8px !important;
  margin: 0 !important;
  padding: 0 !important;
  justify-content: center !important;
}

.category_btn,
.category_btn button {
  background: #991b1b !important;
  color: #ffffff !important;
  border: none !important;
  border-radius: 10px !important;
  font-weight: 800 !important;
  font-size: 12px !important;
  line-height: 1 !important;
  height: 32px !important;
  min-height: 32px !important;
  padding: 0 8px !important;
  box-shadow: 0 6px 14px rgba(0,0,0,0.18) !important;
}

.category_btn:hover,
.category_btn button:hover {
  background: #7f1d1d !important;
  color: #ffffff !important;
}

/* ===== VAULT PDF READER — FINAL CLEAN VERSION ===== */

.pdf_mobile_fallback {
  display: none;
  margin-bottom: 12px;
}

.pdf_fallback_btn {
  display: inline-block;
  background: #dc2626;
  color: #ffffff !important;
  text-decoration: none !important;
  border-radius: 12px;
  padding: 12px 16px;
  font-weight: 800;
}

.pdf_fallback_btn:hover {
  background: #b91c1c;
}

.pdf_viewer_wrap {
  width: 100%;
  border: 1px solid rgba(148,163,184,0.16);
  border-radius: 14px;
  overflow: hidden;
  background: rgba(2,6,23,0.95);
}

.pdf_viewer_iframe {
  width: 100%;
  height: 620px;
  border: none;
  display: block;
  background: #ffffff;
}

/* ===== MOBILE BEHAVIOR ===== */

@media (max-width: 767px) {

  /* Show the red "Open PDF" button on phones */
  .pdf_mobile_fallback {
    display: block;
  }

  /* Hide embedded reader on phones (causes Google errors) */
  .pdf_desktop_embed {
    display: none;
  }

  .pdf_viewer_iframe {
    height: 520px;
  }
}

@media (max-width: 767px) {
  #category_button_stack {
    gap: 5px !important;
    margin-bottom: 10px !important;
  }

  #category_button_row {
    gap: 6px !important;
    justify-content: center !important;
  }

  .category_btn,
  .category_btn button {
    font-size: 12px !important;
    height: 30px !important;
    min-height: 30px !important;
    padding: 0 6px !important;
  }
}

html, body {
  max-width: 100vw !important;
  overflow-x: hidden !important;
  overscroll-behavior-x: none !important;
}

body {
  position: relative !important;
}

.gradio-container,
#app_shell,
.results_wrap,
.side_wrap,
.utility_panel_wrap,
.pdf_viewer_wrap,
.promo_shell,
.featured_wrap {
  max-width: 100% !important;
  overflow-x: hidden !important;
}

  .title_main {
    font-size: 24px;
  }

  .title_tag {
    font-size: 11px;
    line-height: 1.45;
  }

  #query_box textarea {
    min-height: 84px !important;
    height: 84px !important;
    max-height: 118px !important;
    font-size: 16px !important;
    padding: 14px 16px !important;
  }

  .featured_row {
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 8px;
  }

  .featured_title {
    font-size: 11px;
    min-height: 30px;
  }
}

#restore_result_box:empty,
#restore_claim_status_box:empty,
#manage_portal_link_box:empty {
  display: none !important;
}

#restore_result_box,
#restore_claim_status_box,
#manage_portal_link_box {
  margin: 0 !important;
  padding: 0 !important;
  min-height: 0 !important;
}

.portal_btn {
  display: inline-block;
  width: 100%;
  text-align: center;
  background: rgba(51,65,85,0.95);
  color: #ffffff !important;
  text-decoration: none !important;
  border: 1px solid rgba(148,163,184,0.18);
  border-radius: 12px;
  padding: 12px 16px;
  font-weight: 800;
}

.portal_btn:hover {
  background: rgba(71,85,105,0.98);
  color: #ffffff !important;
}
"""

with gr.Blocks(css=CUSTOM_CSS, head=CUSTOM_HEAD, title="Universal Library Vault") as app:
    state = gr.State(vault_state)
    promo_state_store = gr.State(promo_state)
    utility_panel_store = gr.State(make_utility_panel_state())
    category_panel_store = gr.State(make_category_panel_state())
    membership_store = gr.BrowserState(
        make_membership_state(),
        storage_key="fmv_membership_state_v1"
    )

    user_session_store = gr.BrowserState(
        make_user_session_state(),
        storage_key="fmv_user_session_v1"
    )

    stripe_flash_store = gr.BrowserState(
        make_stripe_flash_state(),
        storage_key="fmv_stripe_flash_v1"
    )

    restore_claim_store = gr.State(make_restore_claim_state())

    usage_store = gr.BrowserState(
        usage_state,
        storage_key="fmv_usage_state_v1",
        secret="FMV_USAGE_STATE_SECRET_V1"
    )

    selected_pdf_store = gr.State({
        "title": "",
        "full_url": "",
        "preview_url": "",
        "preview_ready": False,
        "is_premium_only": False,
        "free_preview_enabled": True
    })

    with gr.Column(elem_id="app_shell"):
        gr.HTML("""
        <div class="title_wrap">
          <div class="title_main">Universal Library Vault</div>
          <div class="title_tag">
            Unveal the hidden architecture of over 2,800 PDF Books from the 15th-21st century.<br>
            The #1 Library Index full of forbidden books and buried transmissions. Search freely.
          </div>
        </div>
        """)

        with gr.Group(elem_classes=["top_utility_wrap"]):
            with gr.Row(elem_classes=["top_utility_row"]):
                membership_toggle_btn = gr.Button("Membership", elem_classes=["top_utility_btn"])
                favorites_toggle_btn = gr.Button("Favorites", elem_classes=["top_utility_btn"])
                restore_toggle_btn = gr.Button("Premium", elem_classes=["top_utility_btn"])

            with gr.Column(visible=False, elem_classes=["utility_panel_wrap"]) as membership_panel:

                gr.HTML('<div class="utility_panel_title">Membership</div>')

                with gr.Group(elem_classes=["membership_section_card"]):
                    gr.HTML('<div class="membership_section_heading">Create Account</div>')

                    signup_email_input = gr.Textbox(
                        label="Email",
                        placeholder="Enter your email",
                        lines=1
                    )
                    signup_password_input = gr.Textbox(
                        label="Password",
                        placeholder="Create a password",
                        lines=1,
                        type="password"
                    )
                    signup_confirm_password_input = gr.Textbox(
                        label="Confirm Password",
                        placeholder="Confirm your password",
                        lines=1,
                        type="password"
                    )
                    sign_up_btn = gr.Button("Sign Up", elem_classes=["membership_primary_btn"])

                with gr.Group(elem_classes=["membership_section_card"]):
                    gr.HTML('<div class="membership_section_heading">Log In</div>')

                    login_email_input = gr.Textbox(
                        label="Email",
                        placeholder="Enter your email",
                        lines=1
                    )
                    login_password_input = gr.Textbox(
                        label="Password",
                        placeholder="Enter your password",
                        lines=1,
                        type="password"
                    )

                    with gr.Row():
                        log_in_btn = gr.Button("Log In", elem_classes=["membership_primary_btn"])
                        log_out_btn = gr.Button("Log Out", elem_classes=["membership_secondary_btn"])

                account_result_box = gr.HTML(value="")


            with gr.Column(visible=False, elem_classes=["utility_panel_wrap"]) as favorites_panel:

                gr.HTML('<div class="utility_panel_title">Favorites</div>')

                save_favorite_btn = gr.Button("Save Selected to Favorites")

                favorites_selector = gr.Radio(
                    choices=[],
                    value=None,
                    label="Saved Favorite Books",
                    interactive=True,
                    elem_id="favorites_selector_radio"
                )

                remove_favorite_btn = gr.Button("Remove Selected Favorite")

                favorites_status_box = gr.HTML(value="")
                continue_reading_box = gr.HTML(value="")
                continue_reading_btn = gr.Button("Resume Last Book", elem_id="continue_reading_btn")
                favorites_result_box = gr.HTML(value="")


            with gr.Column(visible=False, elem_classes=["utility_panel_wrap"]) as restore_panel:

                gr.HTML('<div class="utility_panel_title">Restore Premium</div>')

                restore_input = gr.Textbox(
                    label="Stripe Email, Customer ID, or Subscription ID",
                    placeholder="Enter your Stripe email, cus_..., or sub_...",
                    lines=1
                )
                restore_btn = gr.Button("Restore Premium")
                manage_portal_btn = gr.Button("Manage Billing / Cancel", elem_classes=["membership_secondary_btn"])
                manage_portal_link_box = gr.HTML(value="", elem_id="manage_portal_link_box", visible=False)
                restore_result_box = gr.HTML(value="", elem_id="restore_result_box", visible=False)
                restore_claim_status_box = gr.HTML(value="", elem_id="restore_claim_status_box", visible=False)

                restore_password_email_input = gr.Textbox(
                    label="Premium Account Email",
                    placeholder="Restored premium email",
                    lines=1
                )
                restore_password_input = gr.Textbox(
                    label="New Password",
                    placeholder="Create your password",
                    lines=1,
                    type="password"
                )
                restore_confirm_password_input = gr.Textbox(
                    label="Confirm Password",
                    placeholder="Confirm your password",
                    lines=1,
                    type="password"
                )
                set_restored_password_btn = gr.Button("Set Password")


        account_status_box = gr.HTML(value="")

        query_box = gr.Textbox(
            lines=3,
            max_lines=6,
            label="Matrix Access Input",
            placeholder=BROWSE_HINT,
            elem_id="query_box",
            show_label=False,
            container=True,
            interactive=True,
            value="",
            scale=1
        )


        browse_btn = gr.Button("Browse the Library Vault", elem_id="browse_btn")

        gr.HTML('<div class="category_section_label">Explore by Category</div>')

        category_btns = []

        with gr.Column(elem_id="category_button_stack"):
            for row_cats in [top_categories[:4], top_categories[4:8]]:
                with gr.Row(elem_id="category_button_row", equal_height=True):
                    for cat in row_cats:
                        btn = gr.Button(cat, elem_classes=["category_btn"])
                        category_btns.append((btn, cat))

        result_box = gr.HTML()

        result_selector = gr.Radio(
            choices=[],
            value=None,
            label="Browse the Library Vault",
            interactive=True,
            elem_id="result_selector_radio"
        )

        open_pdf_btn = gr.Button("Open Selected PDF", elem_id="open_pdf_btn")
        access_box = gr.HTML(value='<div class="results_wrap"></div>')
        stripe_return_box = gr.HTML(value="")
        membership_status_box = gr.HTML(value="", visible=False)
        pdf_viewer_box = gr.HTML(value="")

        featured_shelf_box = gr.HTML(value=initial_featured_html)

        daily_jewel_box = gr.HTML(value=initial_daily_html)
        refresh_jewel_btn = gr.Button("Refresh Jewel", elem_id="refresh_btn")
        promo_box = gr.HTML(value=initial_promo_html)
        promo_timer = gr.Timer(value=8.0, active=False)

        membership_debug_box = gr.JSON(label="Membership State", visible=False)

        if DEV_MODE:
            with gr.Accordion("Developer Access Controls", open=False):
                premium_on_btn = gr.Button("Enable Premium")
                premium_off_btn = gr.Button("Disable Premium")
                reset_usage_btn = gr.Button("Reset Daily Opens")


    membership_toggle_btn.click(
        fn=toggle_membership_panel,
        inputs=[utility_panel_store],
        outputs=[membership_panel, favorites_panel, restore_panel, utility_panel_store]
    )

    favorites_toggle_btn.click(
        fn=toggle_favorites_panel,
        inputs=[utility_panel_store],
        outputs=[membership_panel, favorites_panel, restore_panel, utility_panel_store]
    )

    restore_toggle_btn.click(
        fn=toggle_restore_panel,
        inputs=[utility_panel_store],
        outputs=[membership_panel, favorites_panel, restore_panel, utility_panel_store]
    )

    continue_reading_btn.click(
        fn=get_continue_reading_book,
        inputs=[user_session_store],
        outputs=[selected_pdf_store]
    ).then(
        fn=handle_book_open_with_continue,
        inputs=[selected_pdf_store, usage_store, membership_store, user_session_store],
        outputs=[access_box, usage_store, pdf_viewer_box, continue_reading_box]
    )

    browse_btn.click(
        fn=browse_matrix_vault,
        inputs=query_box,
        outputs=[result_selector]
    )

    open_pdf_btn.click(
        fn=handle_book_open_with_continue,
        inputs=[selected_pdf_store, usage_store, membership_store, user_session_store],
        outputs=[access_box, usage_store, pdf_viewer_box, continue_reading_box]
    )

    query_box.submit(
        fn=browse_matrix_vault,
        inputs=query_box,
        outputs=[result_selector]
    )

    for btn, cat in category_btns:
        btn.click(
            fn=lambda state, c=cat: toggle_category_results(c, state),
            inputs=[category_panel_store],
            outputs=[result_selector, category_panel_store]
        )

    refresh_jewel_btn.click(
        fn=refresh_vault_panels,
        inputs=[state],
        outputs=[daily_jewel_box, featured_shelf_box, state]
    )

    sign_up_btn.click(
        fn=sign_up_member_ui,
        inputs=[signup_email_input, signup_password_input, signup_confirm_password_input, user_session_store, membership_store],
        outputs=[user_session_store, membership_debug_box, account_status_box, account_result_box, favorites_selector, favorites_status_box, continue_reading_box]
    )

    log_in_btn.click(
        fn=log_in_member_ui,
        inputs=[login_email_input, login_password_input, user_session_store, membership_store],
        outputs=[user_session_store, membership_debug_box, account_status_box, account_result_box, favorites_selector, favorites_status_box, continue_reading_box]
    )

    log_out_btn.click(
        fn=log_out_member_ui,
        inputs=[user_session_store, membership_store],
        outputs=[user_session_store, membership_debug_box, account_status_box, account_result_box, favorites_selector, favorites_status_box, continue_reading_box]
    )

    restore_btn.click(
        fn=restore_premium_access_ui,
        inputs=[restore_input, membership_store, user_session_store, restore_claim_store],
        outputs=[
            membership_store,
            membership_debug_box,
            membership_status_box,
            restore_result_box,
            account_status_box,
            favorites_selector,
            favorites_status_box,
            user_session_store,
            restore_claim_store,
            restore_claim_status_box
        ]
    ).then(
        fn=lambda state: normalize_restore_claim_state(state).get("email", ""),
        inputs=[restore_claim_store],
        outputs=[restore_password_email_input]
    )

    manage_portal_btn.click(
        fn=create_customer_portal_html,
        inputs=[membership_store, user_session_store],
        outputs=[manage_portal_link_box]
    )

    set_restored_password_btn.click(
        fn=set_restored_premium_password,
        inputs=[
            restore_password_email_input,
            restore_password_input,
            restore_confirm_password_input,
            user_session_store,
            membership_store,
            restore_claim_store
        ],
        outputs=[
            user_session_store,
            membership_debug_box,
            account_status_box,
            restore_result_box,
            restore_claim_store,
            restore_claim_status_box
        ]
    )

    save_favorite_btn.click(
        fn=save_selected_to_favorites,
        inputs=[selected_pdf_store, user_session_store, membership_store],
        outputs=[user_session_store, favorites_selector, favorites_status_box, favorites_result_box]
    )

    remove_favorite_btn.click(
        fn=remove_selected_favorite,
        inputs=[favorites_selector, user_session_store, membership_store],
        outputs=[user_session_store, favorites_selector, favorites_status_box, favorites_result_box]
    )

    favorites_selector.change(
        fn=select_favorite_by_title,
        inputs=[favorites_selector, user_session_store],
        outputs=[selected_pdf_store]
    ).then(
        fn=handle_book_open_with_continue,
        inputs=[selected_pdf_store, usage_store, membership_store, user_session_store],
        outputs=[access_box, usage_store, pdf_viewer_box, continue_reading_box]
    )

    if DEV_MODE:
        premium_on_btn.click(
            fn=enable_premium,
            inputs=[membership_store],
            outputs=[membership_store, membership_status_box]
        )

        premium_off_btn.click(
            fn=disable_premium,
            inputs=[membership_store],
            outputs=[membership_store, membership_status_box]
        )

        reset_usage_btn.click(
            fn=reset_daily_opens,
            inputs=[usage_store],
            outputs=[usage_store, access_box]
        )

    result_selector.change(
        fn=select_book_by_title,
        inputs=[result_selector],
        outputs=[selected_pdf_store]
    )

    app.load(
        fn=restore_access_on_load,
        inputs=[usage_store, membership_store],
        outputs=[usage_store, access_box]
    )

    app.load(
        fn=restore_account_and_favorites_on_load,
        inputs=[user_session_store, membership_store],
        outputs=[user_session_store, account_status_box, membership_debug_box, favorites_selector, favorites_status_box, continue_reading_box]
    )

    app.load(
        fn=lambda: (
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(visible=False),
            make_utility_panel_state()
        ),
        inputs=None,
        outputs=[membership_panel, favorites_panel, restore_panel, utility_panel_store]
    )

    app.load(
        fn=lambda: (
            gr.update(visible=False, value=""),
            make_restore_claim_state(),
            gr.update(visible=False, value="")
        ),
        inputs=None,
        outputs=[restore_result_box, restore_claim_store, restore_claim_status_box]
    )

# =========================================================
# FASTAPI PRODUCTION SERVER
# =========================================================

server = FastAPI(title="Universal Library Vault")

@server.get("/health")
async def healthcheck():
    return {"status": "ok"}

@server.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=500, detail="Missing STRIPE_WEBHOOK_SECRET.")

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=sig_header,
            secret=STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid webhook payload.")
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid webhook signature.")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Webhook error: {str(e)}")

    try:
        process_stripe_webhook_event(event)
    except Exception as e:
        print("WEBHOOK PROCESSING ERROR:", str(e))
        raise HTTPException(status_code=500, detail="Webhook processing failed.")

    return JSONResponse({"received": True}, status_code=200)

app = gr.mount_gradio_app(
    app=server,
    blocks=app,
    path="/"
)
