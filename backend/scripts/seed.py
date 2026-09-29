import asyncio
from datetime import date, timedelta
from decimal import Decimal
import sys
import os

# Add parent path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select
from app.core.logging import logger
from app.core.security import hash_password
from app.database.session import AsyncSessionLocal
from app.models.user import Role, User
from app.models.customer import Customer
from app.models.vehicle import VehicleBrand, VehicleModel, VehicleVariant, Accessory
from app.models.inventory import InventoryUnit
from app.models.offer import Offer
from app.models.lead import Lead, LeadStatusHistory


async def seed():
    logger.info("Starting database seed script...")
    async with AsyncSessionLocal() as session:
        # 1. Seed Roles
        roles_data = [
            ("ADMIN", "System administrator with full permissions"),
            ("DEALER_OWNER", "Dealership owner with management access and analytics"),
            ("SALES_EXECUTIVE", "Front-office sales representative managing customers and quotations"),
            ("FINANCE_EXECUTIVE", "Loan and finance specialist handling EMI approvals"),
            ("INVENTORY_MANAGER", "Warehouse and stock controller managing chassis units"),
        ]
        roles_map = {}
        for name, desc in roles_data:
            stmt = select(Role).where(Role.name == name)
            role = (await session.execute(stmt)).scalars().first()
            if not role:
                role = Role(name=name, description=desc)
                session.add(role)
                await session.flush()
                logger.info(f"Created role: {name}")
            roles_map[name] = role

        # 2. Seed Default Users (Development credentials)
        users_data = [
            ("admin@dealership.com", "Admin User", "9876500001", "AdminPass123!", "ADMIN"),
            ("owner@dealership.com", "Rajesh Singhania (Owner)", "9876500002", "OwnerPass123!", "DEALER_OWNER"),
            ("sales@dealership.com", "Vikram Malhotra (Sales)", "9876500003", "SalesPass123!", "SALES_EXECUTIVE"),
            ("finance@dealership.com", "Ananya Deshmukh (Finance)", "9876500004", "FinancePass123!", "FINANCE_EXECUTIVE"),
            ("inventory@dealership.com", "Suresh Kumar (Inventory)", "9876500005", "InventoryPass123!", "INVENTORY_MANAGER"),
        ]
        users_map = {}
        for email, name, phone, pwd, r_name in users_data:
            stmt = select(User).where(User.email == email)
            user = (await session.execute(stmt)).scalars().first()
            if not user:
                user = User(
                    email=email,
                    full_name=name,
                    phone=phone,
                    password_hash=hash_password(pwd),
                    role_id=roles_map[r_name].id,
                    is_active=True,
                )
                session.add(user)
                await session.flush()
                logger.info(f"Created user: {email} ({r_name}) [Password: {pwd}]")
            users_map[email] = user

        # 3. Seed Vehicle Brands
        brands_data = [
            ("Honda", "Japan", "https://assets.dealership.com/brands/honda.png"),
            ("TVS", "India", "https://assets.dealership.com/brands/tvs.png"),
            ("Yamaha", "Japan", "https://assets.dealership.com/brands/yamaha.png"),
            ("Ather Energy", "India", "https://assets.dealership.com/brands/ather.png"),
        ]
        brands_map = {}
        for b_name, country, logo in brands_data:
            stmt = select(VehicleBrand).where(VehicleBrand.name == b_name)
            brand = (await session.execute(stmt)).scalars().first()
            if not brand:
                brand = VehicleBrand(name=b_name, country=country, logo_url=logo, is_active=True)
                session.add(brand)
                await session.flush()
                logger.info(f"Created brand: {b_name}")
            brands_map[b_name] = brand

        # 4. Seed Models
        models_data = [
            ("Activa", "Honda", "SCOOTER", "India's best-selling 110-125cc family scooter"),
            ("Jupiter", "TVS", "SCOOTER", "Practical commuter scooter with MetalMax body and large boot"),
            ("Aerox 155", "Yamaha", "SCOOTER", "Maxi-sports liquid-cooled 155cc performance scooter"),
            ("450X", "Ather Energy", "EV", "High-performance smart electric scooter with Google Maps navigation"),
        ]
        models_map = {}
        for m_name, b_name, v_type, desc in models_data:
            stmt = select(VehicleModel).where(VehicleModel.name == m_name)
            model = (await session.execute(stmt)).scalars().first()
            if not model:
                model = VehicleModel(
                    brand_id=brands_map[b_name].id,
                    name=m_name,
                    vehicle_type=v_type,
                    description=desc,
                    is_active=True,
                )
                session.add(model)
                await session.flush()
                logger.info(f"Created model: {m_name}")
            models_map[m_name] = model

        # 5. Seed Variants
        variants_data = [
            (
                "Activa 125 DLX",
                "Activa",
                "HONDA-ACTIVA-125-DLX",
                124,
                "PETROL",
                "AUTOMATIC",
                Decimal("89000.00"),
                Decimal("6500.00"),
                Decimal("9200.00"),
                Decimal("2500.00"),
                "Pearl Nightstar Black, Rebel Red Metallic",
            ),
            (
                "Jupiter 125 Disc",
                "Jupiter",
                "TVS-JUPITER-125-DISC",
                124,
                "PETROL",
                "AUTOMATIC",
                Decimal("86500.00"),
                Decimal("6400.00"),
                Decimal("8800.00"),
                Decimal("2400.00"),
                "Titanium Grey, Dawn Orange, Indiblue",
            ),
            (
                "Aerox 155 MotoGP Edition",
                "Aerox 155",
                "YAMAHA-AEROX-155-GP",
                155,
                "PETROL",
                "CVT",
                Decimal("148000.00"),
                Decimal("9500.00"),
                Decimal("15000.00"),
                Decimal("3500.00"),
                "Monster Energy Racing Blue",
            ),
            (
                "Ather 450X Pro Pack",
                "450X",
                "ATHER-450X-PRO",
                None,
                "ELECTRIC",
                "AUTOMATIC",
                Decimal("138000.00"),
                Decimal("7200.00"),
                Decimal("4000.00"),
                Decimal("2800.00"),
                "Space Grey, True Red, Salt Green",
            ),
        ]
        variants_map = {}
        for v_name, m_name, code, cc, fuel, trans, ex_sh, ins, rto, acc, colors in variants_data:
            stmt = select(VehicleVariant).where(VehicleVariant.code == code)
            variant = (await session.execute(stmt)).scalars().first()
            if not variant:
                on_road = ex_sh + ins + rto + acc
                variant = VehicleVariant(
                    model_id=models_map[m_name].id,
                    name=v_name,
                    code=code,
                    engine_cc=cc,
                    fuel_type=fuel,
                    transmission=trans,
                    ex_showroom_price=ex_sh,
                    insurance_price=ins,
                    rto_price=rto,
                    accessories_price=acc,
                    on_road_price=on_road,
                    color_options=colors,
                    status="ACTIVE",
                    specs_json={"mileage_kmpl": 55 if fuel == "PETROL" else None, "range_km": 110 if fuel == "ELECTRIC" else None},
                )
                session.add(variant)
                await session.flush()
                logger.info(f"Created variant: {v_name} (On-Road: Rs. {on_road:,.2f})")
            variants_map[code] = variant

        # 6. Seed Accessories
        acc_data = [
            ("All-Weather Full Body Cover", "ACC-BC-01", Decimal("950.00")),
            ("Stainless Steel Perimeter Guard", "ACC-PG-02", Decimal("1450.00")),
            ("Anti-Skid Heavy Duty Floor Mat", "ACC-FM-03", Decimal("550.00")),
            ("Smart Mobile Phone Handlebar Mount", "ACC-MM-04", Decimal("800.00")),
            ("Premium Steel Helmet Lock", "ACC-HL-05", Decimal("650.00")),
        ]
        for a_name, part_no, price in acc_data:
            stmt = select(Accessory).where(Accessory.name == a_name)
            if not (await session.execute(stmt)).scalars().first():
                acc = Accessory(name=a_name, part_number=part_no, price=price, is_active=True)
                session.add(acc)
                logger.info(f"Created accessory: {a_name}")

        # 7. Seed Physical Inventory Units
        inv_units_data = [
            ("HONDA-ACTIVA-125-DLX", "ME4JF5077K810001", "JF50E910001", "Pearl Nightstar Black", "AVAILABLE"),
            ("HONDA-ACTIVA-125-DLX", "ME4JF5077K810002", "JF50E910002", "Rebel Red Metallic", "AVAILABLE"),
            ("HONDA-ACTIVA-125-DLX", "ME4JF5077K810003", "JF50E910003", "Pearl Nightstar Black", "RESERVED"),
            ("TVS-JUPITER-125-DISC", "MD626CG45N220001", "CG45E820001", "Titanium Grey", "AVAILABLE"),
            ("TVS-JUPITER-125-DISC", "MD626CG45N220002", "CG45E820002", "Dawn Orange", "AVAILABLE"),
            ("YAMAHA-AEROX-155-GP", "ME1SG5310M430001", "G3M5E730001", "Monster Energy Racing Blue", "AVAILABLE"),
            ("ATHER-450X-PRO", "MATHER450X400001", "ATHE4E640001", "Space Grey", "AVAILABLE"),
            ("ATHER-450X-PRO", "MATHER450X400002", "ATHE4E640002", "True Red", "AVAILABLE"),
        ]
        for v_code, vin, eng, col, st in inv_units_data:
            stmt = select(InventoryUnit).where(InventoryUnit.vin_chassis_number == vin)
            if not (await session.execute(stmt)).scalars().first():
                unit = InventoryUnit(
                    variant_id=variants_map[v_code].id,
                    vin_chassis_number=vin,
                    engine_number=eng,
                    color=col,
                    status=st,
                    branch_name="Main Showroom",
                    arrival_date=date.today() - timedelta(days=10),
                )
                session.add(unit)
                logger.info(f"Created inventory unit: VIN {vin} ({st})")

        # 8. Seed Offers
        today = date.today()
        offers_data = [
            (
                "Festive Season Mega Savings",
                "Flat ₹5,000 cash discount on any purchase above ₹80,000",
                "FIXED",
                Decimal("5000.00"),
                today - timedelta(days=5),
                today + timedelta(days=45),
                Decimal("80000.00"),
            ),
            (
                "Green Mobility EV Subsidy Bonus",
                "Additional ₹7,500 dealership subsidy on all Ather electric scooters",
                "FIXED",
                Decimal("7500.00"),
                today - timedelta(days=10),
                today + timedelta(days=60),
                Decimal("100000.00"),
            ),
            (
                "College Student Youth Perk",
                "5% off on base showroom price for student registrations",
                "PERCENTAGE",
                Decimal("5.00"),
                today - timedelta(days=10),
                today + timedelta(days=60),
                Decimal("0.00"),
            ),
        ]
        for title, desc, d_type, d_val, v_from, v_to, min_val in offers_data:
            stmt = select(Offer).where(Offer.title == title)
            if not (await session.execute(stmt)).scalars().first():
                offer = Offer(
                    title=title,
                    description=desc,
                    discount_type=d_type,
                    discount_value=d_val,
                    valid_from=v_from,
                    valid_until=v_to,
                    min_order_value=min_val,
                    active=True,
                )
                session.add(offer)
                logger.info(f"Created offer: {title}")

        # 9. Seed Sample Customers & Leads
        sample_customers = [
            ("Rahul Sharma", "9876543210", "rahul.sharma@example.com", "Flat 302, Palm Heights", "Mumbai", "Maharashtra", "400001", "Software Engineer", Decimal("120000.00")),
            ("Priya Patel", "9876543211", "priya.patel@example.com", "B-12 Shanti Nagar", "Ahmedabad", "Gujarat", "380001", "Architect", Decimal("160000.00")),
            ("Amit Verma", "9876543212", "amit.verma@example.com", "Sector 14, Huda Colony", "Gurugram", "Haryana", "122001", "Consultant", Decimal("100000.00")),
        ]
        for fname, phone, email, addr, city, state, pin, occ, bgt in sample_customers:
            stmt = select(Customer).where(Customer.phone == phone)
            cust = (await session.execute(stmt)).scalars().first()
            if not cust:
                cust = Customer(
                    full_name=fname,
                    phone=phone,
                    email=email,
                    address=addr,
                    city=city,
                    state=state,
                    pincode=pin,
                    occupation=occ,
                    preferred_language="English",
                    budget=bgt,
                )
                session.add(cust)
                await session.flush()
                logger.info(f"Created customer: {fname}")

                # Create sample lead
                lead = Lead(
                    customer_id=cust.id,
                    assigned_salesperson_id=users_map["sales@dealership.com"].id,
                    source="WALK_IN",
                    status="INTERESTED",
                    priority="HIGH",
                    interested_variant_id=variants_map["HONDA-ACTIVA-125-DLX"].id,
                    expected_purchase_date=date.today() + timedelta(days=14),
                    notes="Interested in Activa 125 DLX with loan finance options.",
                )
                session.add(lead)
                await session.flush()

                history = LeadStatusHistory(
                    lead_id=lead.id,
                    from_status="NONE",
                    to_status="INTERESTED",
                    changed_by_user_id=users_map["sales@dealership.com"].id,
                    reason="Initial showroom consultation",
                )
                session.add(history)

        await session.commit()
        logger.info("Database seeding successfully completed!")


if __name__ == "__main__":
    asyncio.run(seed())
