"""Built-in example pull requests for the demo UI.

Each example is a realistic before/after change set, assembled into the same
PullRequest objects the real pipeline produces, so the UI exercises the actual
classifier and risk scorer rather than canned output.
"""
from __future__ import annotations

import ast
import difflib
from dataclasses import dataclass
from typing import Callable

from .classifier import classify_pr
from .models import FileChange, PullRequest


def change(path: str, old: str | None, new: str | None) -> FileChange:
    """Build a FileChange (with a real unified diff) from two versions of a file."""
    old_lines = (old or "").splitlines()
    new_lines = (new or "").splitlines()
    diff = list(difflib.unified_diff(
        old_lines, new_lines, fromfile=f"a/{path}", tofile=f"b/{path}", lineterm="", n=3))
    body = diff[2:]
    return FileChange(
        path=path,
        status="added" if old is None else "removed" if new is None else "modified",
        additions=sum(1 for ln in body if ln.startswith("+")),
        deletions=sum(1 for ln in body if ln.startswith("-")),
        patch="\n".join(diff),
        old_source=old,
        new_source=new,
    )


def tidy(src: str) -> str:
    """Re-format Python the way an auto-formatter would. The AST is identical by construction."""
    return ast.unparse(ast.parse(src)) + "\n"


@dataclass(frozen=True)
class Example:
    key: str
    title: str
    blurb: str
    build: Callable[[], PullRequest]


# ---------------------------------------------------------------- A: pre-release tidy-up

def _lock(bump: bool) -> str:
    names = [f"package-{i:02d}" for i in range(60)]
    out = []
    for i, n in enumerate(names):
        minor = 4 if (bump and i % 3 != 2) else 3
        out.append(f'[[package]]\nname = "{n}"\nversion = "1.{minor}.0"\n')
    return "\n".join(out)


def _tidy_up() -> PullRequest:
    utils_old = '''import re,json

def slugify(text):
    text=text.lower().strip()
    return re.sub(r'[^a-z0-9]+','-',text).strip('-')

def load_json(path):
    with open(path) as f:
        return json.load(f)

def chunks(seq,n):
    for i in range(0,len(seq),n):
        yield seq[i:i+n]
'''
    pricing_old = '''def calc(p, q):
    return p * q


def discount(p, pct):
    return p - p * pct / 100


def invoice_line(name, p, q):
    return f"{name}: {calc(p, q)}"


def invoice_total(lines):
    return sum(calc(p, q) for _, p, q in lines)
'''
    pricing_new = '''def compute_total(price, quantity):
    return price * quantity


def discount(price, pct):
    return price - price * pct / 100


def invoice_line(name, price, quantity):
    return f"{name}: {compute_total(price, quantity)}"


def invoice_total(lines):
    return sum(compute_total(price, quantity) for _, price, quantity in lines)
'''
    models_old = '''class Customer:
    def __init__(self, name, email):
        self.name = name
        self.email = email

    def display_name(self):
        return self.name.title()

    def domain(self):
        return self.email.split("@")[1]
'''
    models_new = '''class Customer:
    """A person who places orders."""

    def __init__(self, name, email):
        self.name = name
        self.email = email

    def display_name(self):
        """Name in Title Case, for greetings and receipts."""
        return self.name.title()

    def domain(self):
        """The part of the email address after the @."""
        return self.email.split("@")[1]
'''
    pr = PullRequest(
        title="Pre-release tidy-up",
        body="Formatting pass, clearer names in pricing, docstrings, refreshed lockfile.",
        author="priya", author_prior_prs=42,
        files=[
            change("app/utils.py", utils_old, tidy(utils_old)),
            change("app/pricing.py", pricing_old, pricing_new),
            change("app/models.py", models_old, models_new),
            change("README.md", "# Shop\n\nA small shop backend.\n", "# Shop\n\nA small shop backend.\n\n## Running\n\nRun `make dev`.\n"),
            change("poetry.lock", _lock(False), _lock(True)),
        ],
    )
    return classify_pr(pr)


# ---------------------------------------------------------------- B: login rewrite, no tests

def _login_rewrite() -> PullRequest:
    session_old = '''import hashlib
import time

SESSIONS = {}


def create_session(user_id):
    token = hashlib.md5(f"{user_id}{time.time()}".encode()).hexdigest()
    SESSIONS[token] = user_id
    return token


def get_user(token):
    return SESSIONS.get(token)


def end_session(token):
    SESSIONS.pop(token, None)
'''
    session_new = '''import hashlib
import secrets
import time

SESSIONS = {}
TTL_SECONDS = 3600
REMEMBER_ME_TTL = 30 * 24 * 3600


def create_session(user_id, remember_me=False):
    token = secrets.token_urlsafe(32)
    ttl = REMEMBER_ME_TTL if remember_me else TTL_SECONDS
    SESSIONS[hash_token(token)] = {"user": user_id, "expires": time.time() + ttl}
    return token


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def get_user(token):
    entry = SESSIONS.get(hash_token(token))
    if entry is None:
        return None
    if entry["expires"] < time.time():
        end_session(token)
        return None
    return entry["user"]


def refresh_session(token, extend_by=TTL_SECONDS):
    entry = SESSIONS.get(hash_token(token))
    if entry:
        entry["expires"] = time.time() + extend_by


def end_session(token):
    SESSIONS.pop(hash_token(token), None)
'''
    charge_old = '''def charge(card, amount_cents):
    result = gateway.post("/charges", card=card, amount=amount_cents)
    if result.status != "ok":
        raise PaymentError(result.message)
    return result.id
'''
    charge_new = '''import time

MAX_RETRIES = 3


def charge(card, amount_cents, idempotency_key=None):
    last_error = None
    for attempt in range(MAX_RETRIES):
        result = gateway.post("/charges", card=card, amount=amount_cents,
                              key=idempotency_key)
        if result.status == "ok":
            return result.id
        last_error = result.message
        time.sleep(2 ** attempt)
    raise PaymentError(last_error)
'''
    migration = (
        "ALTER TABLE sessions ADD COLUMN token_hash CHAR(64);\n"
        "ALTER TABLE sessions ADD COLUMN expires_at TIMESTAMP;\n"
        "UPDATE sessions SET expires_at = NOW() + INTERVAL '1 hour';\n"
        "ALTER TABLE sessions DROP COLUMN token;\n"
    )
    pr = PullRequest(
        title="Rework login sessions and payment retries",
        body="Hash session tokens, add expiry and remember-me, retry failed charges.",
        author="new-contributor", author_prior_prs=0,
        file_prior_fixes={"src/auth/session.py": 4},
        files=[
            change("src/auth/session.py", session_old, session_new),
            change("src/payments/charge.py", charge_old, charge_new),
            change("migrations/0042_add_session_token.sql", None, migration),
            change("requirements.txt", "flask==3.0.0\nrequests==2.31.0\n", "flask==3.0.0\nrequests==2.31.0\npyjwt==2.8.0\n"),
        ],
    )
    return classify_pr(pr)


# ---------------------------------------------------------------- C: needle in a reformat

def _reformat_with_needle() -> PullRequest:
    cart = '''import  os,sys
from decimal import Decimal

class Cart:
    def __init__(self,items=None):
        self.items=items or []
        self.coupon=None

    def add(self,item,qty=1):
        self.items.append({'item':item,'qty':qty})
        return  len(self.items)

    def remove(self,sku):
        self.items=[r for r in self.items if r['item']['sku']!=sku]

    def total(self):
        t=Decimal('0')
        for row in self.items:
            t+=Decimal(str(row['item']['price']))*row['qty']
        if self.coupon:
            t=t-Decimal(str(self.coupon))
        return max(t,Decimal('0'))
'''
    orders = '''import datetime,uuid

def new_order(cart,customer):
    return {'id':str(uuid.uuid4()),'customer':customer,'lines':list(cart.items),'created':datetime.datetime.utcnow().isoformat(),'status':'pending'}

def mark_paid(order,ref):
    order['status']='paid'
    order['payment_ref']=ref
    return order

def mark_shipped(order,carrier,tracking):
    if order['status']!='paid':
        raise ValueError('order not paid')
    order.update({'status':'shipped','carrier':carrier,'tracking':tracking})
    return order

def summary(order):
    return '%s (%d lines) - %s'%(order['id'][:8],len(order['lines']),order['status'])
'''
    inventory = '''STOCK={}

def load(rows):
    for r in rows:
        STOCK[r['sku']]=int(r['qty'])

def available(sku,qty=1):
    return STOCK.get(sku,0)>=qty

def reserve(sku,qty):
    if not available(sku,qty):
        raise KeyError('out of stock: '+sku)
    STOCK[sku]-=qty
    return STOCK[sku]

def restock(sku,qty):
    STOCK[sku]=STOCK.get(sku,0)+qty
'''
    shipping = '''RATES={'standard':4.95,'express':12.5,'overnight':29.0}

def quote(method,weight_kg):
    base=RATES[method]
    if weight_kg>5:
        base+= (weight_kg-5)*1.2
    return round(base,2)

def eta_days(method):
    return {'standard':5,'express':2,'overnight':1}[method]
'''
    emails = '''def receipt(order):
    lines=['Thanks for your order!','']
    for l in order['lines']:
        lines.append('- %s x%d'%(l['item']['name'],l['qty']))
    return '\\n'.join(lines)

def shipped(order):
    return 'Your order %s is on its way via %s.'%(order['id'][:8],order['carrier'])
'''
    discounts_old = '''def free_shipping(total, is_member):
    """Orders of $50 or more ship free; members always do."""
    if is_member:
        return True
    return total >= 50


def loyalty_discount(years):
    if years < 1:
        return 0
    return min(years * 2, 20)
'''
    discounts_new = discounts_old.replace("total >= 50", "total > 50").replace("years < 1", "years <= 1")
    pr = PullRequest(
        title="Apply auto-formatter",
        body="Runs the formatter over the shop package. No functional changes.",
        author="sam", author_prior_prs=2,
        file_prior_fixes={"shop/billing/discounts.py": 3},
        files=[
            change("shop/cart.py", cart, tidy(cart)),
            change("shop/orders.py", orders, tidy(orders)),
            change("shop/inventory.py", inventory, tidy(inventory)),
            change("shop/billing/discounts.py", discounts_old, discounts_new),
            change("shop/shipping.py", shipping, tidy(shipping)),
            change("shop/emails.py", emails, tidy(emails)),
        ],
    )
    return classify_pr(pr)


# ---------------------------------------------------------------- D: feature with tests

def _csv_export() -> PullRequest:
    export = '''import csv
import io

from .models import Report

COLUMNS = ["id", "title", "owner", "created", "status"]


def _clean(value):
    text = "" if value is None else str(value)
    # Prevent spreadsheet formula injection
    if text[:1] in ("=", "+", "-", "@"):
        return "'" + text
    return text


def rows_for(report):
    for item in report.items:
        yield [_clean(getattr(item, col, "")) for col in COLUMNS]


def to_csv(report):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(COLUMNS)
    for row in rows_for(report):
        writer.writerow(row)
    return buf.getvalue()


def filename_for(report):
    safe = "".join(c if c.isalnum() else "-" for c in report.title.lower())
    return f"{safe.strip('-')}-{report.created:%Y%m%d}.csv"
'''
    views_old = '''from flask import Blueprint, render_template

from .models import Report

bp = Blueprint("reports", __name__)


@bp.route("/reports/<int:report_id>")
def show(report_id):
    report = Report.get(report_id)
    return render_template("report.html", report=report)
'''
    views_new = '''from flask import Blueprint, Response, abort, render_template

from .export import filename_for, to_csv
from .models import Report

bp = Blueprint("reports", __name__)


@bp.route("/reports/<int:report_id>")
def show(report_id):
    report = Report.get(report_id)
    return render_template("report.html", report=report)


@bp.route("/reports/<int:report_id>/export.csv")
def export(report_id):
    report = Report.get(report_id)
    if report is None:
        abort(404)
    body = to_csv(report)
    headers = {"Content-Disposition": f"attachment; filename={filename_for(report)}"}
    return Response(body, mimetype="text/csv", headers=headers)
'''
    tests = '''from reports.export import _clean, to_csv


class Item:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeReport:
    title = "Q3 Sales"
    items = [Item(id=1, title="Deal", owner="ana", created="2026-01-01", status="won")]


def test_formula_injection_is_neutralised():
    assert _clean("=SUM(A1)") == "'=SUM(A1)"


def test_csv_has_header_and_row():
    lines = to_csv(FakeReport()).strip().splitlines()
    assert lines[0] == "id,title,owner,created,status"
    assert len(lines) == 2
'''
    pyproject_old = '[project]\nname = "shop-reports"\ndependencies = [\n    "flask>=3.0",\n    "sqlalchemy>=2.0",\n]\n'
    pyproject_new = pyproject_old.replace("flask>=3.0", "flask>=3.0.3")
    pr = PullRequest(
        title="Add CSV export to reports",
        body="Adds a download button for reports. Includes tests for formula-injection safety.",
        author="jordan", author_prior_prs=3,
        file_prior_fixes={"src/reports/views.py": 4},
        files=[
            change("src/reports/export.py", None, export),
            change("src/reports/views.py", views_old, views_new),
            change("tests/test_export.py", None, tests),
            change("pyproject.toml", pyproject_old, pyproject_new),
        ],
    )
    return classify_pr(pr)


EXAMPLES: list[Example] = [
    Example("needle", "Hidden bugs in a reformat",
            "An auto-formatter touches six files. Two one-character edits to discount rules are mixed in.",
            _reformat_with_needle),
    Example("login", "Login rewrite, no tests",
            "Changes how sessions and card charges work, runs a database migration, adds a dependency. No tests.",
            _login_rewrite),
    Example("tidy", "Pre-release tidy-up",
            "Formatting, a rename, docstrings and a refreshed lockfile. No code behavior changes.",
            _tidy_up),
    Example("feature", "CSV export with tests",
            "A feature from a fairly new contributor, covered by tests.",
            _csv_export),
]
EXAMPLES_BY_KEY = {e.key: e for e in EXAMPLES}
