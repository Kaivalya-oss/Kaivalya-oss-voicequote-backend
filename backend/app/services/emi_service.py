from decimal import Decimal, ROUND_HALF_UP, getcontext
from app.core.exceptions import BadRequestException
from app.schemas.finance import EMICalculateRequest, EMICalculateResponse

# Set Decimal precision to 28 for banking accuracy
getcontext().prec = 28


class EMIService:
    @staticmethod
    def calculate(
        principal: Decimal,
        annual_interest_rate: Decimal,
        tenure_months: int,
    ) -> EMICalculateResponse:
        """
        Calculate Equated Monthly Installment (EMI) using banking standard formulas with Decimal precision.
        Formula: EMI = [P * r * (1 + r)^n] / [(1 + r)^n - 1]
        where:
          P = Principal
          r = Monthly interest rate (annual_interest_rate / 12 / 100)
          n = Tenure in months
        """
        if principal <= Decimal("0"):
            raise BadRequestException("INVALID_PRINCIPAL", "Principal loan amount must be greater than zero.")
        if annual_interest_rate < Decimal("0"):
            raise BadRequestException("INVALID_INTEREST_RATE", "Annual interest rate must be greater than or equal to zero.")
        if tenure_months <= 0:
            raise BadRequestException("INVALID_TENURE", "Tenure must be at least 1 month.")

        P = principal
        n = tenure_months

        # Zero interest case (e.g. 0% festive financing)
        if annual_interest_rate == Decimal("0"):
            monthly_emi = (P / Decimal(n)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            total_payable = P.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            total_interest = Decimal("0.00")
        else:
            # Monthly rate
            r = annual_interest_rate / Decimal("1200")
            one_plus_r = Decimal("1") + r
            pow_factor = one_plus_r ** n

            # EMI formula
            numerator = P * r * pow_factor
            denominator = pow_factor - Decimal("1")
            calculated_emi = numerator / denominator

            monthly_emi = calculated_emi.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            total_payable = (monthly_emi * Decimal(n)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            total_interest = (total_payable - P).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        return EMICalculateResponse(
            monthly_emi=monthly_emi,
            total_interest=total_interest,
            total_payable=total_payable,
            principal=P.quantize(Decimal("0.01")),
            tenure_months=n,
            annual_interest_rate=annual_interest_rate.quantize(Decimal("0.01")),
        )
