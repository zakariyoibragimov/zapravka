import asyncio
import os
import sys
from datetime import date

# Add current directory to path
sys.path.append(os.getcwd())

from app.db.session import AsyncSessionLocal
from app.models import Client, BonusExpiry
from sqlalchemy import select, delete

async def setup():
    async with AsyncSessionLocal() as session:
        # Client setup
        q = select(Client).where(Client.phone == '79009990001')
        result = await session.execute(q)
        client = result.scalars().first()
        
        if not client:
            client = Client(phone='79009990001', bonus_balance=40.00)
            session.add(client)
            await session.flush()
        else:
            client.bonus_balance = 40.00
            
        # Clear old expiry
        await session.execute(delete(BonusExpiry).where(BonusExpiry.client_id == client.id))
        
        # New expiry for today
        expiry = BonusExpiry(
            client_id=client.id,
            bonus_amount=40.00,
            remaining=40.00,
            expiry_date=date.today()
        )
        session.add(expiry)
        await session.commit()
        print(f"Set up client {client.phone} with 40.00 bonuses expiring today.")

if __name__ == '__main__':
    asyncio.run(setup())
