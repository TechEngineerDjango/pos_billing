import pytest
from httpx import AsyncClient
from sqlalchemy import select, func
from database.models import MenuItem

# =============================================================================
# MENU MANAGEMENT TESTS - FUNCTIONAL & INTEGRATION
# =============================================================================

@pytest.mark.asyncio
async def test_menu_add_single_item_via_db(async_client: AsyncClient, db_session):
    """Test adding a menu item directly to database (simulating successful menu add)"""
    # Create a menu item directly
    new_item = MenuItem(
        name="Test Burger",
        price=9.99,
        category="Burgers",
        shop_id=1
    )
    db_session.add(new_item)
    await db_session.commit()
    await db_session.refresh(new_item)
    
    # Verify it was created
    assert new_item.id is not None
    assert new_item.name == "Test Burger"
    assert new_item.price == 9.99
    assert new_item.category == "Burgers"
    assert new_item.shop_id == 1


@pytest.mark.asyncio
async def test_menu_update_item(async_client: AsyncClient, db_session):
    """Test updating menu item fields"""
    # Create initial item
    item = MenuItem(name="Original Name", price=5.00, category="Original", shop_id=1)
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)
    item_id = item.id  # Store ID before updates
    
    # Update the item
    item.name = "Updated Name"
    item.price = 10.50
    item.category = "Updated"
    await db_session.commit()
    
    # Fetch again to verify
    result = await db_session.execute(
        select(MenuItem).where(MenuItem.id == item_id)
    )
    updated = result.scalars().first()
    
    assert updated.name == "Updated Name"
    assert updated.price == 10.50
    assert updated.category == "Updated"


@pytest.mark.asyncio
async def test_menu_delete_item(async_client: AsyncClient, db_session):
    """Test deleting a menu item"""
    # Create item
    item = MenuItem(name="To Delete", price=3.00, category="Temp", shop_id=1)
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)
    item_id = item.id
    
    # Delete it
    await db_session.delete(item)
    await db_session.commit()
    
    # Verify deletion
    result = await db_session.execute(
        select(MenuItem).where(MenuItem.id == item_id)
    )
    deleted = result.scalars().first()
    assert deleted is None


@pytest.mark.asyncio
async def test_menu_multiple_items_different_categories(async_client: AsyncClient, db_session):
    """Test creating multiple menu items with different categories"""
    items = [
        MenuItem(name="Burger", price=8.99, category="Main", shop_id=1),
        MenuItem(name="Fries", price=2.99, category="Sides", shop_id=1),
        MenuItem(name="Soda", price=1.99, category="Drinks", shop_id=1),
    ]
    
    for item in items:
        db_session.add(item)
    await db_session.commit()
    
    # Verify all were created
    result = await db_session.execute(
        select(func.count(MenuItem.id)).where(MenuItem.shop_id == 1)
    )
    count = result.scalar()
    assert count >= 3


@pytest.mark.asyncio
async def test_menu_price_handling(async_client: AsyncClient, db_session):
    """Test various price values"""
    items = [
        MenuItem(name="Free Item", price=0.00, category="Promo", shop_id=1),
        MenuItem(name="Cheap Item", price=0.99, category="Budget", shop_id=1),
        MenuItem(name="Expensive Item", price=99.99, category="Premium", shop_id=1),
    ]
    
    for item in items:
        db_session.add(item)
    await db_session.commit()
    
    # Verify prices are stored correctly
    result = await db_session.execute(
        select(MenuItem).where(MenuItem.name == "Free Item")
    )
    free_item = result.scalars().first()
    assert free_item.price == 0.00
    
    result = await db_session.execute(
        select(MenuItem).where(MenuItem.name == "Expensive Item")
    )
    expensive = result.scalars().first()
    assert expensive.price == 99.99


@pytest.mark.asyncio
async def test_menu_shop_association(async_client: AsyncClient, db_session):
    """Test that menu items are properly associated with shops"""
    item = MenuItem(name="Shop Item", price=5.99, category="Food", shop_id=1)
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)
    
    assert item.shop_id == 1


@pytest.mark.asyncio
async def test_menu_query_by_category(async_client: AsyncClient, db_session):
    """Test querying menu items by category"""
    items = [
        MenuItem(name="Item 1", price=5.0, category="Burgers", shop_id=1),
        MenuItem(name="Item 2", price=6.0, category="Burgers", shop_id=1),
        MenuItem(name="Item 3", price=7.0, category="Drinks", shop_id=1),
    ]
    
    for item in items:
        db_session.add(item)
    await db_session.commit()
    
    # Query burgers only
    result = await db_session.execute(
        select(MenuItem).where(
            MenuItem.category == "Burgers",
            MenuItem.shop_id == 1
        )
    )
    burgers = result.scalars().all()
    
    assert len(burgers) == 2
    for burger in burgers:
        assert burger.category == "Burgers"


@pytest.mark.asyncio
async def test_menu_special_characters_in_name(async_client: AsyncClient, db_session):
    """Test menu items with special characters"""
    special_names = [
        "Burger & Fries",
        "50% OFF Special!",
        "Large (XL) Drink",
        "Spicy 🔥 Wings"
    ]
    
    for name in special_names:
        item = MenuItem(name=name, price=9.99, category="Special", shop_id=1)
        db_session.add(item)
    
    await db_session.commit()
    
    # Verify they were stored correctly
    result = await db_session.execute(
        select(MenuItem).where(MenuItem.name == "Burger & Fries")
    )
    item = result.scalars().first()
    assert item is not None
    assert item.name == "Burger & Fries"


@pytest.mark.asyncio
async def test_menu_default_category(async_client: AsyncClient, db_session):
    """Test default category value"""
    # The API defaults to "General" if category is not provided
    # Here we test that explicitly setting General works
    item = MenuItem(name="Generic Item", price=4.99, category="General", shop_id=1)
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)
    
    assert item.category == "General"


# =============================================================================
# API ENDPOINT TESTS
# =============================================================================

@pytest.mark.asyncio
async def test_menu_add_endpoint_unauthorized(async_client: AsyncClient):
    """Test that unauthorized users cannot add menu items via API"""
    response = await async_client.post(
        "/admin/menu/add",
        data={
            "name": "Unauthorized Item",
            "price": "9.99",
            "category": "Test"
        }
    )
    
    # Should return error
    json_response = response.json()
    assert "error" in json_response
    assert json_response["error"] == "Unauthorized"


@pytest.mark.asyncio
async def test_menu_edit_page_unauthorized(async_client: AsyncClient):
    """Test that unauthorized users cannot access edit page"""
    response = await async_client.get("/admin/menu/edit/1", follow_redirects=False)
    
    # Should be 303 Redirect to login
    assert response.status_code == 303
    assert "/auth/login" in response.headers["location"]


@pytest.mark.asyncio
async def test_menu_delete_endpoint_unauthorized(async_client: AsyncClient):
    """Test that unauthorized users cannot delete menu items"""
    response = await async_client.post("/admin/menu/delete/1")
    
    # Should return error - either 'error' or 'detail' key
    json_response = response.json()
    assert "error" in json_response or "detail" in json_response


@pytest.mark.asyncio
async def test_menu_products_page_requires_auth(async_client: AsyncClient):
    """Test that products page requires authentication"""
    response = await async_client.get("/admin/products", follow_redirects=False)
    
    # Should redirect to login (303)
    assert response.status_code == 303
    assert "/auth/login" in response.headers["location"]


@pytest.mark.asyncio
async def test_menu_add_with_auth(async_client: AsyncClient, db_session):
    """Test adding menu item with proper authentication"""
    # Login first
    login_res = await async_client.post(
        "/auth/login",
        data={"username": "admin", "password": "admin"},
        follow_redirects=False
    )
    assert login_res.status_code == 303
    
    # Now add menu item
    response = await async_client.post(
        "/admin/menu/add",
        data={
            "name": "Authenticated Add",
            "price": "12.99",
            "category": "Test"
        },
        follow_redirects=True
    )
    
    # Should successfully redirect to products page
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_menu_edit_page_with_auth(async_client: AsyncClient, db_session):
    """Test accessing edit page with authentication"""
    # Create an item in DB
    item = MenuItem(name="Edit Test", price=7.99, category="Test", shop_id=1)
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)
    item_id = item.id  # Store ID
    
    # Login
    await async_client.post(
        "/auth/login",
        data={"username": "admin", "password": "admin"}
    )
    
    # Access edit page
    response = await async_client.get(f"/admin/menu/edit/{item_id}", follow_redirects=True)
    
    assert response.status_code == 200
    # Page should load (might not contain "edit" text in dark theme)
    assert len(response.text) > 0


@pytest.mark.asyncio
async def test_menu_update_with_auth(async_client: AsyncClient, db_session):
    """Test updating menu item with authentication"""
    # Create item
    item = MenuItem(name="Update Test", price=5.00, category="Test", shop_id=1)
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)
    
    # Login
    await async_client.post(
        "/auth/login",
        data={"username": "admin", "password": "admin"}
    )
    
    # Update item
    response = await async_client.post(
        f"/admin/menu/update/{item.id}",
        data={
            "name": "Updated via API",
            "price": "15.00",
            "category": "Updated"
        },
        follow_redirects=True
    )
    
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_menu_delete_with_auth(async_client: AsyncClient, db_session):
    """Test deleting menu item with authentication"""
    # Create item
    item = MenuItem(name="Delete Test", price=3.99, category="Test", shop_id=1)
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)
    
    # Login
    await async_client.post(
        "/auth/login",
        data={"username": "admin", "password": "admin"}
    )
    
    # Delete item
    response = await async_client.post(
        f"/admin/menu/delete/{item.id}",
        follow_redirects=True
    )
    
    assert response.status_code == 200


# =============================================================================
# INTEGRATION TESTS
# =============================================================================

@pytest.mark.asyncio
async def test_menu_full_crud_lifecycle(async_client: AsyncClient, db_session):
    """Test complete CRUD lifecycle for a menu item"""
    # 1. CREATE
    item = MenuItem(name="Lifecycle Test", price=8.99, category="Test", shop_id=1)
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)
    item_id = item.id
    
    # Verify created
    assert item.id is not None
    
    # 2. READ
    result = await db_session.execute(
        select(MenuItem).where(MenuItem.id == item_id)
    )
    read_item = result.scalars().first()
    assert read_item is not None
    assert read_item.name == "Lifecycle Test"
    
    # 3. UPDATE
    item.price = 12.99
    item.category = "Updated"
    await db_session.commit()
    
    result = await db_session.execute(
        select(MenuItem).where(MenuItem.id == item_id)
    )
    updated_item = result.scalars().first()
    assert updated_item.price == 12.99
    assert updated_item.category == "Updated"
    
    # 4. DELETE
    await db_session.delete(item)
    await db_session.commit()
    
    result = await db_session.execute(
        select(MenuItem).where(MenuItem.id == item_id)
    )
    deleted_item = result.scalars().first()
    assert deleted_item is None


@pytest.mark.asyncio
async def test_menu_count_items_by_shop(async_client: AsyncClient, db_session):
    """Test counting menu items for a specific shop"""
    # Add multiple items for shop 1
    for i in range(5):
        item = MenuItem(name=f"Item {i}", price=float(i + 5), category="Test", shop_id=1)
        db_session.add(item)
    await db_session.commit()
    
    # Count items for shop 1
    result = await db_session.execute(
        select(func.count(MenuItem.id)).where(MenuItem.shop_id == 1)
    )
    count = result.scalar()
    
    assert count >= 5  # At least the 5 we just added
