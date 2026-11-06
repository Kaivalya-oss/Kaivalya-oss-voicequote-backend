"""The development sign-in accounts, and the rule that keeps them out of everything but local work.

Their passwords are published in this repository. Outside development and tests they are refused
everywhere: they cannot be seeded or created, and cannot sign in even if a database already holds them.
"""
from typing import Optional

from app.core.config import DEV_ENVIRONMENTS, settings

# email, full name, phone, password, role
DEV_ACCOUNTS = (
    ("admin@dealership.com", "Admin User", "9876500001", "AdminPass123!", "ADMIN"),
    ("owner@dealership.com", "Rajesh Singhania (Owner)", "9876500002", "OwnerPass123!", "DEALER_OWNER"),
    ("sales@dealership.com", "Vikram Malhotra (Sales)", "9876500003", "SalesPass123!", "SALES_EXECUTIVE"),
    ("finance@dealership.com", "Ananya Deshmukh (Finance)", "9876500004", "FinancePass123!", "FINANCE_EXECUTIVE"),
    ("inventory@dealership.com", "Suresh Kumar (Inventory)", "9876500005", "InventoryPass123!", "INVENTORY_MANAGER"),
)
_DEV_EMAILS = frozenset(account[0] for account in DEV_ACCOUNTS)
_DEV_PASSWORDS = frozenset(account[3] for account in DEV_ACCOUNTS)


def dev_accounts_permitted() -> bool:
    # An allow-list, so a mistyped or unfamiliar environment name is treated as production.
    return settings.APP_ENV.strip().lower() in DEV_ENVIRONMENTS


def is_refused_dev_credential(email: str, password: Optional[str] = None) -> bool:
    if dev_accounts_permitted():
        return False
    return email.strip().lower() in _DEV_EMAILS or password in _DEV_PASSWORDS
