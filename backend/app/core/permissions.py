from enum import Enum
from typing import List, Set


class RoleEnum(str, Enum):
    ADMIN = "ADMIN"
    DEALER_OWNER = "DEALER_OWNER"
    SALES_EXECUTIVE = "SALES_EXECUTIVE"
    FINANCE_EXECUTIVE = "FINANCE_EXECUTIVE"
    INVENTORY_MANAGER = "INVENTORY_MANAGER"


# Convenience groups
ALL_ROLES: Set[str] = {
    RoleEnum.ADMIN.value,
    RoleEnum.DEALER_OWNER.value,
    RoleEnum.SALES_EXECUTIVE.value,
    RoleEnum.FINANCE_EXECUTIVE.value,
    RoleEnum.INVENTORY_MANAGER.value,
}

MANAGEMENT_ROLES: Set[str] = {
    RoleEnum.ADMIN.value,
    RoleEnum.DEALER_OWNER.value,
}

SALES_ROLES: Set[str] = {
    RoleEnum.ADMIN.value,
    RoleEnum.DEALER_OWNER.value,
    RoleEnum.SALES_EXECUTIVE.value,
}

FINANCE_ROLES: Set[str] = {
    RoleEnum.ADMIN.value,
    RoleEnum.DEALER_OWNER.value,
    RoleEnum.FINANCE_EXECUTIVE.value,
}

INVENTORY_ROLES: Set[str] = {
    RoleEnum.ADMIN.value,
    RoleEnum.DEALER_OWNER.value,
    RoleEnum.INVENTORY_MANAGER.value,
}
