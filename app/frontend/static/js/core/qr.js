// Client-side UPI QR generation — no external network call, no payment
// details sent to a third party. Wraps the vendored qrcode-generator lib
// (app/frontend/static/vendor/qrcode/qrcode.js).
function buildUpiLink(upiId, name, amount) {
    return `upi://pay?pa=${upiId}&pn=${encodeURIComponent(name || '')}&am=${amount}&cu=INR`;
}

function upiQrDataUrl(upiId, name, amount) {
    if (!upiId) return null;
    const qr = qrcode(0, 'M');
    qr.addData(buildUpiLink(upiId, name, amount));
    qr.make();
    return qr.createDataURL(6, 4);
}
