import asyncio
from httpx import ASGITransport, AsyncClient
from app.main import app
import time

async def test_create_cashier():
    timestamp = int(time.time())
    username = f'ztest_{timestamp}'
    
    transport = ASGITransport(app=app, raise_app_exceptions=True)
    async with AsyncClient(transport=transport, base_url='http://test') as ac:
        # Step 1: Login using JSON instead of form data if 422 occurred
        # Wait, the error message indicates it expects a dict/object. 
        # But OAuth2PasswordRequestForm usually expects form-data.
        # Let's try JSON just in case, but usually it's application/x-www-form-urlencoded.
        
        login_resp = await ac.post('/api/admin/login', data={'username': 'admin', 'password': 'admin1234'})
        if login_resp.status_code == 422:
             login_resp = await ac.post('/api/admin/login', json={'username': 'admin', 'password': 'admin1234'})
        
        print(f'Login status: {login_resp.status_code}')
        if login_resp.status_code != 200:
            print(f'Login failed: {login_resp.text}')
            return
            
        token = login_resp.json().get('access_token')
        headers = {'Authorization': f'Bearer {token}'}
        
        # Step 2: Create cashier
        cashier_data = {
            'username': username,
            'name': 'Test Cashier',
            'password': 'secret123',
            'azs_id': 1
        }
        
        try:
            create_resp = await ac.post('/api/admin/cashiers', json=cashier_data, headers=headers)
            print(f'Create status: {create_resp.status_code}')
            print(f'Create response: {create_resp.text}')
        except Exception as e:
            print(f'Exception caught during create_resp: {e}')
            import traceback
            traceback.print_exc()

if __name__ == '__main__':
    asyncio.run(test_create_cashier())
