from app.domains.credit.strategies.credit_terms import CreditTerms
from app.domains.credit.strategies.weekly_credit_terms import WeeklyCreditTerms
from app.domains.credit.strategies.monthly_credit_terms import MonthlyCreditTerms
from app.domains.credit.strategies.net_days_credit_terms import NetDaysCreditTerms

_CREDIT_TERMS_REGISTRY = {
    "weekly": WeeklyCreditTerms,
    "monthly": MonthlyCreditTerms,
    "net_days": NetDaysCreditTerms,
}

def get_payment_term_strategy(term_type: str, term_value: int) -> CreditTerms:
    strategy_class = _CREDIT_TERMS_REGISTRY.get(term_type)
    if not strategy_class:
        raise ValueError(f"Unrecognized payment term type: {term_type}")
    return strategy_class(term_value)
