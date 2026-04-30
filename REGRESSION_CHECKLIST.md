# Manual Regression Test Checklist

## Server & Basic Access
- [ ] Server starts without errors
- [ ] Login page loads at /auth/login
- [ ] Can login with admin/admin
- [ ] Redirects to correct page after login

## Admin Dashboard
- [ ] Dashboard loads at /admin/ without errors
- [ ] "Design" tab is visible
- [ ] Customization controls are present:
  - [ ] Logo size slider
  - [ ] Watermark opacity
  - [ ] Background color
  - [ ] Header color
  - [ ] Price card background (Issue #2 fix)
  - [ ] Header text color (Issue #3 fix)
  - [ ] Cart background (Issue #4 fix)
- [ ] Live preview iframe shows
- [ ] Save button works
- [ ] Settings persist after save

## POS Interface (/billing/)
- [ ] POS page loads
- [ ] Menu items display
- [ ] Can add items to cart
- [ ] Quantity controls work
- [ ] Customer search works
- [ ] Customer name auto-fills (Issue #5 fix)
- [ ] NEW/FOUND badge displays correctly
- [ ] Payment method selection works
- [ ] Can create bill
- [ ] Bill number generates
- [ ] Total calculates correctly

## User-Reported Issues (Must Verify)
- [ ] Issue #1: Header color only affects navbar (not sidebar)
- [ ] Issue #2: Price tag background is customizable
- [ ] Issue #3: Header menu text color is customizable  
- [ ] Issue #4: Cart/bill panel background is customizable
- [ ] Issue #5: Customer name selection works correctly

## Database
- [ ] Bills save to database
- [ ] Customers save to database
- [ ] Menu items can be added/edited
- [ ] Shop settings persist

## Critical Paths
- [ ] Login → Dashboard → Customize → Save → Verify changes
- [ ] Login → POS → Add items → Select customer → Create bill
- [ ] Login → Menu management → Add product → Verify in POS

## Status
Tests run: Unit tests only
Manual testing: NOT DONE
Browser verification: NOT DONE
Feature validation: NOT DONE

**Conclusion: NOT production ready until manual testing completes**
