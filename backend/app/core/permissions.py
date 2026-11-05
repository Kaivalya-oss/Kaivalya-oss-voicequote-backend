from enum import Enum
from typing import List, Set


class RoleEnum(str, Enum):
    ADMIN = "ADMIN"
    DEALER_OWNER = "DEALER_OWNER"
    SALES_EXECUTIVE = "SALES_EXECUTIVE"
    FINANCE_EXECUTIVE = "FINANCE_EXECUTIVE"
    INVENTORY_MANAGER = "INVENTORY_MANAGER"
    # Showroom roles in multi-tenant mode. Detailed permissions come in a later phase; until
    # then Finance and Inventory screens are reachable by Head of Sales.
    OWNER = "OWNER"
    HEAD_OF_SALES = "HEAD_OF_SALES"
    SALESPERSON = "SALESPERSON"


# Convenience groups
ALL_ROLES: Set[str] = {
    RoleEnum.ADMIN.value,
    RoleEnum.DEALER_OWNER.value,
    RoleEnum.SALES_EXECUTIVE.value,
    RoleEnum.FINANCE_EXECUTIVE.value,
    RoleEnum.INVENTORY_MANAGER.value,
    RoleEnum.OWNER.value,
    RoleEnum.HEAD_OF_SALES.value,
    RoleEnum.SALESPERSON.value,
}

MANAGEMENT_ROLES: Set[str] = {
    RoleEnum.ADMIN.value,
    RoleEnum.DEALER_OWNER.value,
    RoleEnum.OWNER.value,
    RoleEnum.HEAD_OF_SALES.value,
}

SALES_ROLES: Set[str] = {
    RoleEnum.ADMIN.value,
    RoleEnum.DEALER_OWNER.value,
    RoleEnum.OWNER.value,
    RoleEnum.HEAD_OF_SALES.value,
    RoleEnum.SALESPERSON.value,
    RoleEnum.SALES_EXECUTIVE.value,
}

FINANCE_ROLES: Set[str] = {
    RoleEnum.ADMIN.value,
    RoleEnum.DEALER_OWNER.value,
    RoleEnum.OWNER.value,
    RoleEnum.HEAD_OF_SALES.value,
    RoleEnum.FINANCE_EXECUTIVE.value,
}

INVENTORY_ROLES: Set[str] = {
    RoleEnum.ADMIN.value,
    RoleEnum.DEALER_OWNER.value,
    RoleEnum.OWNER.value,
    RoleEnum.HEAD_OF_SALES.value,
    RoleEnum.INVENTORY_MANAGER.value,
}
