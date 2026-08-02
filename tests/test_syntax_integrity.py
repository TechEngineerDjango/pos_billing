import os
import pytest
from dotenv import load_dotenv

load_dotenv()
SUPERADMIN_PASSWORD = os.getenv("TEST_SUPERADMIN_PASSWORD")
OWNER_PASSWORD = os.getenv("TEST_OWNER_PASSWORD")
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_pos_template_integrity(async_client: AsyncClient):
    """Verify pos.html renders correctly and contains the Data Island tag"""
    # Login as admin (who exists in conftest)
    await async_client.post(
        "/auth/login",
        data={"username": "owner", "password": OWNER_PASSWORD},
        follow_redirects=True
    )
    
    # Access POS page
    response = await async_client.get("/billing/", follow_redirects=True)
    assert response.status_code == 200
    
    html = response.text

    # Check for core structural elements we just added/fixed
    assert '<script id="pos-items-data" type="application/json">' in html
    assert '<script src="/static/js/pos/pos-app.js"></script>' in html

    # The consumer of the Data Island lives in the external pos-app.js bundle,
    # not inlined into the page — verify that file still reads the island by id.
    with open("app/frontend/static/js/pos/pos-app.js") as f:
        pos_js = f.read()
    assert "JSON.parse(document.getElementById('pos-items-data').textContent)" in pos_js

    # Ensure no common Jinja syntax remnants from errors
    assert 'itemsData: {{' not in html  # It should be rendered to actual JSON
    assert '{{ items | tojson | safe }' not in html

@pytest.mark.asyncio
async def test_superadmin_dashboard_template_integrity(async_client: AsyncClient):
    """Verify superadmin_dashboard.html renders correctly and contains the Data Island tag"""
    # Login as superadmin
    await async_client.post(
        "/auth/login",
        data={"username": "superadmin", "password": SUPERADMIN_PASSWORD},
        follow_redirects=True
    )
    
    # Access superadmin dashboard
    # Note: the route might be /superadmin/ or /superadmin/dashboard
    response = await async_client.get("/superadmin/")
    if response.status_code == 302:
        response = await async_client.get(response.headers["Location"])
        
    assert response.status_code == 200
    
    html = response.text
    
    # Check for Data Island
    assert '<script id="superadmin-shops-data" type="application/json">' in html
    
    # Ensure no syntax leaks
    assert 'shopsData: {{' not in html
    assert '{{ shops | tojson | safe }' not in html
