from decimal import Decimal
import json
from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import NotFoundException
from app.core.logging import logger
from app.models.lead import Lead
from app.repositories.lead_repository import LeadRepository
from app.repositories.vehicle_repository import VehicleRepository
from app.schemas.ai import (
    LeadInsightsResponse,
    RecommendedVehicle,
    VehicleRecommendationRequest,
    VehicleRecommendationResponse,
)
from app.services.emi_service import EMIService


class AIService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.vehicle_repo = VehicleRepository(session)
        self.lead_repo = LeadRepository(session)

    async def recommend_vehicles(self, req: VehicleRecommendationRequest) -> VehicleRecommendationResponse:
        """
        Produce AI recommendations using Google Gemini if API key is provided,
        or fallback cleanly to our deterministic heuristic scoring engine.
        """
        variants, _ = await self.vehicle_repo.search_variants(
            status="ACTIVE",
            max_price=req.budget * Decimal("1.25") if req.budget else None,
            limit=20,
        )

        if not variants:
            # Fallback to any active variants if budget is too strict
            variants, _ = await self.vehicle_repo.search_variants(status="ACTIVE", limit=10)

        # Attempt Gemini AI analysis if configured
        if settings.GEMINI_API_KEY:
            try:
                from google import genai

                client = genai.Client(api_key=settings.GEMINI_API_KEY)
                catalog_summary = [
                    {
                        "id": v.id,
                        "name": v.name,
                        "brand": v.model.brand.name,
                        "type": v.model.vehicle_type,
                        "engine_cc": v.engine_cc,
                        "fuel_type": v.fuel_type,
                        "on_road_price": str(v.on_road_price),
                    }
                    for v in variants[:8]
                ]

                prompt = (
                    f"You are an expert two-wheeler sales advisor. Analyze this customer request and match the best vehicles from our catalog.\n"
                    f"Customer Budget: {req.budget or 'Flexible'}\n"
                    f"Usage: {req.usage}\n"
                    f"Preferred Brand: {req.preferred_brand or 'Any'}\n"
                    f"Preferred Fuel: {req.preferred_fuel_type or 'Any'}\n"
                    f"Preferences: {req.preferences or 'None'}\n\n"
                    f"Available Catalog:\n{json.dumps(catalog_summary, indent=2)}\n\n"
                    f"Return a JSON object with two fields:\n"
                    f"1. 'ai_analysis': A 2-sentence conversational recommendation rationale.\n"
                    f"2. 'matches': A list of top 3 matched vehicle IDs with a 'fit_score' (1-100) and 'match_reason'."
                )

                response = client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=prompt,
                )
                if response.text:
                    clean_text = response.text.strip()
                    if clean_text.startswith("```json"):
                        clean_text = clean_text[7:]
                    if clean_text.endswith("```"):
                        clean_text = clean_text[:-3]
                    gemini_result = json.loads(clean_text)

                    recommended: List[RecommendedVehicle] = []
                    matched_dict = {m["id"]: m for m in gemini_result.get("matches", []) if "id" in m}

                    for v in variants:
                        if v.id in matched_dict:
                            m_info = matched_dict[v.id]
                            # 36-month EMI estimate (80% loan at 9.5%)
                            loan = v.on_road_price * Decimal("0.80")
                            emi_res = EMIService.calculate(loan, Decimal("9.50"), 36)
                            recommended.append(
                                RecommendedVehicle(
                                    variant_id=v.id,
                                    variant_name=v.name,
                                    brand_name=v.model.brand.name,
                                    engine_cc=v.engine_cc,
                                    fuel_type=v.fuel_type,
                                    on_road_price=v.on_road_price,
                                    estimated_emi=emi_res.monthly_emi,
                                    fit_score=m_info.get("fit_score", 90),
                                    match_reason=m_info.get("match_reason", "Recommended by Gemini AI"),
                                )
                            )
                    if recommended:
                        recommended.sort(key=lambda x: x.fit_score, reverse=True)
                        return VehicleRecommendationResponse(
                            recommendations=recommended,
                            ai_analysis=gemini_result.get("ai_analysis", "Personalized recommendations generated by Google Gemini."),
                            provider="Google Gemini (gemini-2.5-flash)",
                        )
            except Exception as e:
                logger.warning(f"Gemini API invocation failed, falling back to rule engine: {e}")

        # Deterministic Heuristic Engine Fallback
        scored_vehicles: List[RecommendedVehicle] = []
        for v in variants:
            score = 70
            reasons = []

            # Brand match
            if req.preferred_brand and req.preferred_brand.lower() in v.model.brand.name.lower():
                score += 15
                reasons.append(f"Matches your preference for {v.model.brand.name}")

            # Fuel type match
            if req.preferred_fuel_type and req.preferred_fuel_type.upper() == v.fuel_type.upper():
                score += 10
                reasons.append(f"Features {v.fuel_type.lower()} powertrain")

            # Usage heuristics
            if req.usage.upper() == "COMMUTE" and v.model.vehicle_type == "SCOOTER":
                score += 10
                reasons.append("High fuel efficiency and easy urban maneuverability for daily commute")
            elif req.usage.upper() == "TOURING" and (v.engine_cc or 0) >= 150:
                score += 10
                reasons.append(f"High power {v.engine_cc}cc engine ideal for long highway cruising")
            elif req.usage.upper() == "COLLEGE":
                score += 8
                reasons.append("Youthful styling and accessible curb weight")

            # Budget fitness
            if req.budget:
                diff = abs(v.on_road_price - req.budget)
                if diff <= Decimal("15000"):
                    score += 10
                    reasons.append("Fits precisely within your allocated budget range")

            score = min(score, 98)
            match_reason = ". ".join(reasons) if reasons else "Reliable model in active dealership inventory."

            # Calculate 36-month sample EMI (80% loan, 9.5%)
            loan = v.on_road_price * Decimal("0.80")
            emi_calc = EMIService.calculate(loan, Decimal("9.50"), 36)

            scored_vehicles.append(
                RecommendedVehicle(
                    variant_id=v.id,
                    variant_name=v.name,
                    brand_name=v.model.brand.name,
                    engine_cc=v.engine_cc,
                    fuel_type=v.fuel_type,
                    on_road_price=v.on_road_price,
                    estimated_emi=emi_calc.monthly_emi,
                    fit_score=score,
                    match_reason=match_reason,
                )
            )

        scored_vehicles.sort(key=lambda x: x.fit_score, reverse=True)
        top_matches = scored_vehicles[:4]

        analysis = (
            f"Based on your profile for {req.usage.lower()} and "
            f"target budget of {f'Rs. {req.budget:,.2f}' if req.budget else 'flexible parameters'}, "
            f"we have shortlisted our top-performing models that offer optimal comfort, fuel efficiency, and resale value."
        )

        return VehicleRecommendationResponse(
            recommendations=top_matches,
            ai_analysis=analysis,
            provider="Heuristic Rule-Engine (Deterministic Fallback)",
        )

    async def get_lead_insights(self, lead_id: int) -> LeadInsightsResponse:
        lead = await self.lead_repo.get_by_id(lead_id)
        if not lead:
            raise NotFoundException("Lead", lead_id)

        customer = lead.customer
        variant_name = lead.interested_variant.name if lead.interested_variant else "General Two-Wheeler"

        # Attempt Gemini AI analysis if configured
        if settings.GEMINI_API_KEY:
            try:
                from google import genai

                client = genai.Client(api_key=settings.GEMINI_API_KEY)
                prompt = (
                    f"You are a sales manager analyzing a dealership lead:\n"
                    f"Customer Name: {customer.full_name}\n"
                    f"Budget: {customer.budget or 'Unknown'}\n"
                    f"Interested Vehicle: {variant_name}\n"
                    f"Current Status: {lead.status}\n"
                    f"Priority: {lead.priority}\n"
                    f"Source: {lead.source}\n"
                    f"Notes: {lead.notes or 'No special notes'}\n\n"
                    f"Return a JSON object with keys:\n"
                    f"- 'conversion_probability': (HIGH, MEDIUM, LOW)\n"
                    f"- 'interest_summary': 1-2 sentences\n"
                    f"- 'recommended_followup_timing': string\n"
                    f"- 'suggested_next_action': actionable recommendation\n"
                    f"- 'relevant_vehicle_suggestions': array of string names"
                )
                response = client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=prompt,
                )
                if response.text:
                    clean_text = response.text.strip()
                    if clean_text.startswith("```json"):
                        clean_text = clean_text[7:]
                    if clean_text.endswith("```"):
                        clean_text = clean_text[:-3]
                    gemini_result = json.loads(clean_text)
                    return LeadInsightsResponse(
                        lead_id=lead.id,
                        customer_name=customer.full_name,
                        current_status=lead.status,
                        conversion_probability=gemini_result.get("conversion_probability", "MEDIUM"),
                        interest_summary=gemini_result.get("interest_summary", "Customer interested in showroom test ride."),
                        recommended_followup_timing=gemini_result.get("recommended_followup_timing", "Within 24 hours"),
                        suggested_next_action=gemini_result.get("suggested_next_action", "Call to schedule test ride."),
                        relevant_vehicle_suggestions=gemini_result.get("relevant_vehicle_suggestions", [variant_name]),
                        provider="Google Gemini (gemini-2.5-flash)",
                    )
            except Exception as e:
                logger.warning(f"Gemini API lead insight call failed, falling back: {e}")

        # Deterministic Heuristic Insight Fallback
        prob = "MEDIUM"
        if lead.status in ["TEST_RIDE", "NEGOTIATION", "BOOKED"] or lead.priority in ["HIGH", "URGENT"]:
            prob = "HIGH"
        elif lead.status == "LOST":
            prob = "LOW"

        if lead.status == "NEW":
            action = f"Call customer {customer.full_name} to confirm interest in {variant_name} and understand financing preferences."
            timing = "Immediate (Within 4 hours)"
        elif lead.status == "CONTACTED":
            action = f"Invite {customer.full_name} for a scheduled test ride at the showroom."
            timing = "Within 24 to 48 hours"
        elif lead.status == "INTERESTED":
            action = "Generate and send an official PDF quotation with current festive discount over WhatsApp."
            timing = "Within 24 hours"
        elif lead.status == "TEST_RIDE":
            action = "Capture test ride feedback rating and discuss down payment options."
            timing = "Same day after test ride"
        elif lead.status == "NEGOTIATION":
            action = "Offer zero-processing-fee financing scheme to close booking."
            timing = "Within 24 hours"
        elif lead.status == "BOOKED":
            action = "Initiate RTO registration documentation and allocate VIN chassis number."
            timing = "Next 48 hours"
        elif lead.status == "PURCHASED":
            action = "Schedule vehicle delivery ceremony and customer satisfaction follow-up."
            timing = "Delivery date"
        else:
            action = "Re-engage with new festive discount or upgraded exchange offer."
            timing = "In 2 to 4 weeks"

        summary = (
            f"Customer {customer.full_name} is currently in the '{lead.status}' stage regarding {variant_name}. "
            f"Source was {lead.source.lower()} with {lead.priority.lower()} priority."
        )

        return LeadInsightsResponse(
            lead_id=lead.id,
            customer_name=customer.full_name,
            current_status=lead.status,
            conversion_probability=prob,
            interest_summary=summary,
            recommended_followup_timing=timing,
            suggested_next_action=action,
            relevant_vehicle_suggestions=[variant_name, "Activa 125 DLX", "Jupiter 125"],
            provider="Heuristic Rule-Engine (Deterministic Fallback)",
        )
