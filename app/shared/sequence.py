from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Type, Any

async def next_sequence_number(
    db: AsyncSession,
    *,
    model: Type[Any],
    sequence_column: Any,
    scope_filters: dict,
    prefix: str,
    date_key: str,
    padding: int = 4
) -> str:
    query = select(func.count(model.id))
    for key, value in scope_filters.items():
        query = query.where(getattr(model, key) == value)
    
    # Needs to match the prefix-date_key specifically to reset the counter
    query = query.where(sequence_column.startswith(f"{prefix}-{date_key}-"))
    
    result = await db.execute(query)
    count = result.scalar_one() or 0
    
    return f"{prefix}-{date_key}-{count+1:0{padding}d}"
