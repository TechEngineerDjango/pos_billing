from datetime import date, timedelta
from app.domains.credit.strategies.credit_terms import CreditTerms

class WeeklyCreditTerms(CreditTerms):
    def __init__(self, day_of_week: int):
        self.day_of_week = day_of_week
        
    def compute_due_date(self, from_date: date) -> date:
        days_ahead = self.day_of_week - from_date.weekday()
        if days_ahead <= 0:
            days_ahead += 7
        return from_date + timedelta(days=days_ahead)
        
    def compute_next_statement_date(self, period_end: date) -> date:
        return self.compute_due_date(period_end)
