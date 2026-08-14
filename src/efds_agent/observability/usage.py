from dataclasses import dataclass


@dataclass
class ProcessTokenBudget:
    """A deliberately process-local daily token guardrail.

    This protects a single development/worker process. It is not a distributed
    quota and must be replaced by shared accounting before multi-instance
    enforcement is required.
    """

    limit: int
    used: int = 0

    def reserve(self, amount: int) -> bool:
        amount = max(0, amount)
        if self.used + amount > self.limit:
            return False
        self.used += amount
        return True
