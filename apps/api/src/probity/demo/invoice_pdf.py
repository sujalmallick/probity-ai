"""Synthetic invoice PDFs (reportlab). Synthetic data only — no real customer data."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from probity.ingestion.validators import format_inr


def _amt(minor: int) -> str:
    return format_inr(minor).replace("₹", "")


@dataclass
class InvoiceSpec:
    vendor_name: str
    vendor_address: str
    gstin: str
    email: str
    phone: str
    invoice_number: str
    invoice_date: str
    due_date: str
    po_number: str | None
    items: list[tuple[str, int, int]]  # description, qty, unit_price_minor
    account_number: str
    ifsc: str
    bank_name: str
    gst_rate: int = 18
    footer: str | None = None
    extra_lines: list[str] = field(default_factory=list)

    @property
    def subtotal(self) -> int:
        return sum(q * p for _, q, p in self.items)

    @property
    def tax(self) -> int:
        return self.subtotal * self.gst_rate // 100

    @property
    def total(self) -> int:
        return self.subtotal + self.tax


def render(spec: InvoiceSpec, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setTitle(f"Invoice {spec.invoice_number}")
    w, h = A4
    y = h - 50

    def line(text: str, x: float = 50, size: int = 10, bold: bool = False, dy: int = 15) -> None:
        nonlocal y
        c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        c.drawString(x, y, text)
        y -= dy

    line("TAX INVOICE", size=16, bold=True, dy=24)
    line(spec.vendor_name, size=13, bold=True, dy=18)
    line(f"Address: {spec.vendor_address}")
    line(f"GSTIN: {spec.gstin}")
    line(f"Email: {spec.email}")
    line(f"Phone: {spec.phone}", dy=24)
    line(f"Invoice No: {spec.invoice_number}")
    line(f"Invoice Date: {spec.invoice_date}")
    line(f"Due Date: {spec.due_date}")
    if spec.po_number:
        line(f"PO Number: {spec.po_number}")
    line("Bill To: Probity Demo Traders Pvt Ltd, 5th Floor, Baner Road, Pune 411045", dy=26)

    c.setFont("Helvetica-Bold", 10)
    for x, t in ((50, "Description"), (300, "Qty"), (360, "Unit Price"), (460, "Amount")):
        c.drawString(x, y, t)
    y -= 6
    c.line(50, y, w - 50, y)
    y -= 15
    c.setFont("Helvetica", 10)
    for desc, qty, price in spec.items:
        c.drawString(50, y, desc)
        c.drawString(300, y, f"{qty}")
        c.drawString(360, y, f"{_amt(price)}.00" if price % 100 == 0 else _amt(price))
        c.drawString(460, y, f"{_amt(qty * price)}.00" if (qty * price) % 100 == 0 else _amt(qty * price))
        y -= 15
    y -= 10
    def money(v: int) -> str:
        s = _amt(v)
        return s if "." in s else s + ".00"
    line(f"Subtotal: INR {money(spec.subtotal)}", x=330)
    line(f"GST @ {spec.gst_rate}%: INR {money(spec.tax)}", x=330)
    line(f"Total: INR {money(spec.total)}", x=330, bold=True, dy=26)
    line("Bank Details", bold=True)
    line(f"Account Name: {spec.vendor_name}")
    line(f"Account No: {spec.account_number}")
    line(f"IFSC: {spec.ifsc}")
    line(f"Bank Name: {spec.bank_name}", dy=24)
    for extra in spec.extra_lines:
        line(extra, size=8)
    if spec.footer:
        c.setFont("Helvetica", 7)
        c.drawString(50, 40, spec.footer)
    c.showPage()
    c.save()
    return path
