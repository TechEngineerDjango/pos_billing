from datetime import date
import calendar
from app.domains.credit.strategies.credit_terms import CreditTerms

class MonthlyCreditTerms(CreditTerms):
    def __init__(self, day_of_month: int):
        self.day_of_month = day_of_month
        
    def compute_due_date(self, from_date: date) -> date:
        if from_date.month == 12:
            new_month = 1
            new_year = from_date.year + 1
        else:
            new_month = from_date.month + 1
            new_year = from_date.year
            
        _, last_day = calendar.monthrange(new_year, new_month)
        target_day = min(self.day_of_month, last_day)
        
        return date(new_year, new_month, target_day)
        
    def compute_next_statement_date(self, period_end: date) -> date:
        return self.compute_due_date(period_end)
