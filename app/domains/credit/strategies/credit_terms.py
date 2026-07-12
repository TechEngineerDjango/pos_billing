from abc import ABC, abstractmethod
from datetime import date

class CreditTerms(ABC):
    @abstractmethod
    def compute_due_date(self, from_date: date) -> date:
        pass
        
    @abstractmethod
    def compute_next_statement_date(self, period_end: date) -> date:
        pass
