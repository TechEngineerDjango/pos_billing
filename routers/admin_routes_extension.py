

# ============================================================================
# CUSTOMER MANAGEMENT ROUTES
# ============================================================================

@router.post("/customer/add")
async def add_customer(
    name: str = Form(...),
    phone_number: str = Form(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Add a new customer to the shop."""
    if current_user.role not in ["owner", "superadmin"]:
        return {"error": "Unauthorized"}
    
    if not current_user.shop_id:
        return {"error": "No shop assigned"}
    
    # Basic phone number validation (remove spaces, check if it's not empty)
    phone_clean = re.sub(r'\s+', '', phone_number)
    if not phone_clean:
        return {"error": "Invalid phone number"}
    
    new_customer = Customer(
        name=name,
        phone_number=phone_clean,
        shop_id=current_user.shop_id
    )
    
    db.add(new_customer)
    await db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@router.post("/customer/update/{customer_id}")
async def update_customer(
    customer_id: int,
    name: str = Form(...),
    phone_number: str = Form(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Update customer information."""
    if current_user.role not in ["owner", "superadmin"]:
        return {"error": "Unauthorized"}
    
    # Only update customers from user's shop
    query = select(Customer).where(Customer.id == customer_id)
    if current_user.shop_id:
        query = query.where(Customer.shop_id == current_user.shop_id)
    
    result = await db.execute(query)
    customer = result.scalars().first()
    
    if customer:
        customer.name = name
        phone_clean = re.sub(r'\s+', '', phone_number)
        customer.phone_number = phone_clean
        await db.commit()
    
    return RedirectResponse(url="/admin", status_code=303)


@router.post("/customer/delete/{customer_id}")
async def delete_customer(
    customer_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Delete a customer."""
    if current_user.role not in ["owner", "superadmin"]:
        return {"error": "Unauthorized"}
    
    # Only allow deleting customers from user's shop
    if current_user.shop_id:
        await db.execute(
            delete(Customer).where(
                Customer.id == customer_id,
                Customer.shop_id == current_user.shop_id
            )
        )
    else:
        # Superadmin can delete any
        await db.execute(delete(Customer).where(Customer.id == customer_id))
    
    await db.commit()
    return RedirectResponse(url="/admin", status_code=303)


# ============================================================================
# BILL DETAIL VIEW
# ============================================================================

@router.get("/bill/{bill_id}")
async def get_bill_detail(
    bill_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get detailed bill information (API endpoint for AJAX)."""
    if current_user.role not in ["owner", "superadmin"]:
        return JSONResponse({"error": "Unauthorized"}, status_code=403)
    
    # Fetch bill with customer info
    query = select(Bill).where(Bill.id == bill_id)
    if current_user.shop_id and current_user.role != "superadmin":
        query = query.where(Bill.shop_id == current_user.shop_id)
    
    result = await db.execute(query)
    bill = result.scalars().first()
    
    if not bill:
        return JSONResponse({"error": "Bill not found"}, status_code=404)
    
    # Get customer info if exists
    customer_info = None
    if bill.customer_id:
        customer_res = await db.execute(select(Customer).where(Customer.id == bill.customer_id))
        customer = customer_res.scalars().first()
        if customer:
            customer_info = {
                "id": customer.id,
                "name": customer.name,
                "phone_number": customer.phone_number
            }
    
    return JSONResponse({
        "id": bill.id,
        "bill_number": bill.bill_number,
        "total_amount": bill.total_amount,
        "payment_method": bill.payment_method,
        "timestamp": bill.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        "items_snapshot": bill.items_snapshot,
        "customer": customer_info
    })


# ============================================================================
# WHATSAPP BILL SENDING
# ============================================================================

@router.post("/bill/{bill_id}/send-whatsapp")
async def send_bill_whatsapp(
    bill_id: int,
    phone_number: Optional[str] = Form(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Send bill receipt via WhatsApp."""
    if current_user.role not in ["owner", "superadmin"]:
        return JSONResponse({"error": "Unauthorized"}, status_code=403)
    
    # Fetch bill
    query = select(Bill).where(Bill.id == bill_id)
    if current_user.shop_id and current_user.role != "superadmin":
        query = query.where(Bill.shop_id == current_user.shop_id)
    
    result = await db.execute(query)
    bill = result.scalars().first()
    
    if not bill:
        return JSONResponse({"error": "Bill not found"}, status_code=404)
    
    # Determine phone number (from customer or provided)
    target_phone = None
    if bill.customer_id:
        customer_res = await db.execute(select(Customer).where(Customer.id == bill.customer_id))
        customer = customer_res.scalars().first()
        if customer:
            target_phone = customer.phone_number
    
    # Override with provided phone number if given
    if phone_number:
        target_phone = re.sub(r'\s+', '', phone_number)
    
    if not target_phone:
        return JSONResponse({"error": "No phone number available"}, status_code=400)
    
    # Get shop info
    shop = None
    if bill.shop_id:
        shop_res = await db.execute(select(Shop).where(Shop.id == bill.shop_id))
        shop = shop_res.scalars().first()
    
    # Import and use WhatsApp service
    from services.whatsapp import whatsapp_service
    
    bill_data = {
        "bill_number": bill.bill_number,
        "items": bill.items_snapshot,
        "total_amount": bill.total_amount,
        "payment_method": bill.payment_method,
        "timestamp": bill.timestamp.strftime("%Y-%m-%d %H:%M:%S")
    }
    
    shop_data = {
        "name": shop.name if shop else "Shop",
        "address": shop.address if shop else "",
        "currency": shop.currency_symbol if shop else "₹"
    }
    
    try:
        # Generate WhatsApp Deep Link for client-side sending
        whatsapp_url = whatsapp_service.get_whatsapp_url(
            phone_number=target_phone,
            bill_data=bill_data,
            shop_data=shop_data
        )

        # Log server-side "send" (stub)
        await whatsapp_service.send_bill_receipt(
            phone_number=target_phone,
            bill_data=bill_data,
            shop_data=shop_data
        )
        
        return JSONResponse({
            "success": True, 
            "message": "Bill ready to send", 
            "whatsapp_url": whatsapp_url
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
