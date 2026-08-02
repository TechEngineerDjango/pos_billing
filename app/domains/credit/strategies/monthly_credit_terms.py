from datetime import date
import calendar
from app.domains.credit.strategies.credit_terms import CreditTerms


class MonthlyCreditTerms(CreditTerms):
    """Payment term where the customer owes on a fixed day of the month,
    always the month AFTER the reference date — never the same month, even
    if that day hasn't occurred yet this month."""

    def __init__(self, day_of_month: int):
        """day_of_month: the due day (1-31) picked by the customer's terms.
        No range validation here — an out-of-range value silently clamps
        via compute_due_date's min(day_of_month, last_day) instead of
        raising."""
        self.day_of_month = day_of_month

    def compute_due_date(self, from_date: date) -> date:
        """Returns day_of_month in the month immediately after from_date's
        month, clamped to that month's real last day (e.g. day_of_month=31
        in a 30-day month returns the 30th, not an overflow into the next
        month). Rolls December -> January of the following year."""
        if from_date.month == 12:
            new_month = 1
            new_year = from_date.year + 1
        else:
            new_month = from_date.month + 1
            new_year = from_date.year

        # last_day: the real number of days in the target month (28-31),
        # used to clamp day_of_month so date() never raises for e.g. Feb 30.
        _, last_day = calendar.monthrange(new_year, new_month)
        target_day = min(self.day_of_month, last_day)

        return date(new_year, new_month, target_day)

    def compute_next_statement_date(self, period_end: date) -> date:
        """When the customer's NEXT statement should be generated. Same
        formula as compute_due_date today, but a distinct method because it
        feeds a different field (Customer.next_statement_date, the cron
        trigger) than compute_due_date does (the statement/bill's own
        due_date, the payment deadline) — see CreditService.
        generate_statements_for_shop."""
        return self.compute_due_date(period_end)
