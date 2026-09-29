from decimal import Decimal
import pytest
from httpx import AsyncClient

from app.services.emi_service import EMIService


def test_emi_service_unit_calculations():
    # 1. 0% interest loan test
    res_zero = EMIService.calculate(
        principal=Decimal("60000.00"),
        annual_interest_rate=Decimal("0.00"),
        tenure_months=12,
    )
    assert res_zero.monthly_emi == Decimal("5000.00")
    assert res_zero.total_interest == Decimal("0.00")
    assert res_zero.total_payable == Decimal("60000.00")

    # 2. Standard 12% 1-year loan
    res_std = EMIService.calculate(
        principal=Decimal("100000.00"),
        annual_interest_rate=Decimal("12.00"),
        tenure_months=12,
    )
    assert res_std.monthly_emi == Decimal("8884.88")
    assert res_std.total_payable == Decimal("106618.56")
    assert res_std.total_interest == Decimal("6618.56")

    # 3. 3-year (36 months) loan at 9.5%
    res_3yr = EMIService.calculate(
        principal=Decimal("80000.00"),
        annual_interest_rate=Decimal("9.50"),
        tenure_months=36,
    )
    assert res_3yr.monthly_emi > Decimal("0")
    assert res_3yr.total_payable > res_3yr.principal


@pytest.mark.asyncio
async def test_emi_api_endpoint(client: AsyncClient):
    # Valid call
    res = await client.post(
        "/api/v1/finance/calculate-emi",
        json={
            "principal": 100000.00,
            "annual_interest_rate": 10.50,
            "tenure_months": 24,
        },
    )
    assert res.status_code == 200
    data = res.json()["data"]
    assert "monthly_emi" in data
    assert "total_interest" in data
    assert "total_payable" in data
    assert float(data["monthly_emi"]) > 0


@pytest.mark.asyncio
async def test_emi_api_invalid_inputs(client: AsyncClient):
    # Invalid zero principal
    res_zero_p = await client.post(
        "/api/v1/finance/calculate-emi",
        json={"principal": 0, "annual_interest_rate": 10.0, "tenure_months": 12},
    )
    assert res_zero_p.status_code == 422

    # Negative interest rate
    res_neg_r = await client.post(
        "/api/v1/finance/calculate-emi",
        json={"principal": 50000, "annual_interest_rate": -5.0, "tenure_months": 12},
    )
    assert res_neg_r.status_code == 422

    # Zero tenure
    res_zero_t = await client.post(
        "/api/v1/finance/calculate-emi",
        json={"principal": 50000, "annual_interest_rate": 10.0, "tenure_months": 0},
    )
    assert res_zero_t.status_code == 422
