class CustomerBudgetManager:

    async def reserve_tokens(
        self,
        customer_id: str,
        estimated_tokens: int
    ) -> bool:
        ...

    async def release_unused_tokens(
        self,
        customer_id: str,
        reserved: int,
        actual: int
    ):
        ...