"""Record-level (ownership) authorization.

Role checks (`require_roles`) decide *which kinds* of operations a user may perform.
These helpers decide *which records* a user may change:

* Management (ADMIN, DEALER_OWNER) may act on any record.
* A salesperson may act on leads assigned to them (or unassigned leads) and on
  follow-ups they own or that belong to their leads.
* Customers are shared showroom records (walk-ins are reused across salespeople by
  phone). A salesperson may edit a customer nobody owns yet or one they are handling;
  a customer handled only by other salespeople is read-only for them.
* Quotations may be changed only by the salesperson who created them.
* An inventory reservation may be released by whoever made it, or by inventory /
  management roles.

Read scoping (Sales Executives only; other roles are unchanged):

* Lists of leads, follow-ups, quotations and test rides show only the salesperson's own
  records (leads: assigned to them or unassigned).
* Customers cannot be browsed beyond the ones they handle (own lead or quotation). An
  exact phone-number search still returns the matching customer so walk-in reuse at
  intake keeps working; partial searches never reveal other customers.
* Fetching a record outside that scope by ID returns 404, so IDs cannot be probed.
"""
from sqlalchemy import or_, select, union
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenException, NotFoundException
from app.core.permissions import INVENTORY_ROLES, MANAGEMENT_ROLES, RoleEnum
from app.models.customer import Customer
from app.models.followup import Followup
from app.models.inventory import InventoryUnit
from app.models.lead import Lead
from app.models.quotation import Quotation
from app.models.user import User


def is_manager(user: User) -> bool:
    return user.role.name in MANAGEMENT_ROLES


def is_scoped_reader(user: User) -> bool:
    """Roles whose read access is limited to their own sales records."""
    return user.role.name == RoleEnum.SALES_EXECUTIVE.value


def handled_customer_ids(user_id: int):
    """Subquery of customer ids a salesperson handles (registered them, assigned lead or own quotation)."""
    return union(
        select(Customer.id).where(Customer.created_by_user_id == user_id),
        select(Lead.customer_id).where(Lead.assigned_salesperson_id == user_id),
        select(Quotation.customer_id).where(Quotation.sales_executive_id == user_id),
    )


def ensure_can_view_lead(user: User, lead: Lead) -> None:
    if is_scoped_reader(user) and lead.assigned_salesperson_id not in (None, user.id):
        raise NotFoundException("Lead", lead.id)


def ensure_can_view_followup(user: User, followup: Followup, lead: Lead) -> None:
    if not is_scoped_reader(user):
        return
    if followup.assigned_user_id == user.id or lead.assigned_salesperson_id in (None, user.id):
        return
    raise NotFoundException("Followup", followup.id)


def ensure_can_view_quotation(user: User, quotation: Quotation) -> None:
    if is_scoped_reader(user) and quotation.sales_executive_id != user.id:
        raise NotFoundException("Quotation", quotation.id)


def ensure_can_view_owned(user: User, owner_id, resource: str, record_id: int) -> None:
    if is_scoped_reader(user) and owner_id != user.id:
        raise NotFoundException(resource, record_id)


async def ensure_can_view_customer(session: AsyncSession, user: User, customer: Customer) -> None:
    if not is_scoped_reader(user):
        return
    handled = (
        await session.execute(
            select(Customer.id).where(Customer.id == customer.id, Customer.id.in_(handled_customer_ids(user.id)))
        )
    ).scalar_one_or_none()
    if handled is None:
        raise NotFoundException("Customer", customer.id)


def _owner_label(user_obj) -> str:
    return f"'{user_obj.full_name}'" if user_obj is not None else "another salesperson"


def ensure_can_manage_lead(user: User, lead: Lead) -> None:
    if is_manager(user):
        return
    if lead.assigned_salesperson_id in (None, user.id):
        return
    raise ForbiddenException(
        f"Lead #{lead.id} is assigned to {_owner_label(lead.assigned_salesperson)}. "
        "Only the assigned salesperson or a manager can change it."
    )


def ensure_can_assign_lead(user: User, assigned_salesperson_id) -> None:
    """Salespeople may only assign leads to themselves; managers may assign anyone."""
    if assigned_salesperson_id is None or is_manager(user) or assigned_salesperson_id == user.id:
        return
    raise ForbiddenException("Only a manager can assign a lead to another salesperson.")


def ensure_can_manage_followup(user: User, followup: Followup, lead: Lead) -> None:
    if is_manager(user):
        return
    if followup.assigned_user_id == user.id or lead.assigned_salesperson_id in (None, user.id):
        return
    raise ForbiddenException(
        f"Follow-up #{followup.id} belongs to {_owner_label(followup.assigned_user)}. "
        "Only its owner, the lead's salesperson or a manager can change it."
    )


async def ensure_can_edit_customer(session: AsyncSession, user: User, customer: Customer) -> None:
    if is_manager(user):
        return
    lead_owners = (
        await session.execute(
            select(Lead.assigned_salesperson_id).where(
                Lead.customer_id == customer.id, Lead.assigned_salesperson_id.is_not(None)
            )
        )
    ).scalars().all()
    quote_owners = (
        await session.execute(select(Quotation.sales_executive_id).where(Quotation.customer_id == customer.id))
    ).scalars().all()
    owners = set(lead_owners) | set(quote_owners)
    if customer.created_by_user_id is not None:
        owners.add(customer.created_by_user_id)
    if not owners or user.id in owners:
        return
    raise ForbiddenException(
        f"Customer #{customer.id} is handled by another salesperson. Ask them or a manager to update it."
    )


def ensure_can_modify_quotation(user: User, quotation: Quotation) -> None:
    if is_manager(user) or quotation.sales_executive_id == user.id:
        return
    raise ForbiddenException(
        f"Quotation {quotation.quotation_number} was created by another salesperson. "
        "Only its creator or a manager can change it."
    )


def ensure_can_manage_test_ride(user: User, ride) -> None:
    if is_manager(user) or ride.sales_executive_id == user.id:
        return
    raise ForbiddenException(
        f"Test ride #{ride.id} was booked by {_owner_label(ride.sales_executive)}. "
        "Only that salesperson or a manager can change it."
    )


def ensure_can_release_unit(user: User, unit: InventoryUnit) -> None:
    if user.role.name in MANAGEMENT_ROLES or user.role.name in INVENTORY_ROLES:
        return
    if unit.reserved_by_user_id is not None and unit.reserved_by_user_id == user.id:
        return
    raise ForbiddenException(
        f"Unit #{unit.id} was reserved by another user. Only they, inventory or management can release it."
    )
