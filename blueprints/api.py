"""Internal JSON API (i Store backend only — no public reseller API)."""
from flask import Blueprint, jsonify, request, g

from db import get_session
from models import Service
from services import auth as auth_svc

bp = Blueprint("api", __name__, url_prefix="/api")


@bp.route("/services")
def api_services():
    db = get_session()
    try:
        q = request.args.get("q", "").strip()
        query = db.query(Service).filter_by(status="active")
        if q:
            query = query.filter(Service.name.ilike(f"%{q}%"))
        items = query.order_by(Service.sort_order).limit(100).all()
        return jsonify([{
            "id": s.id, "name": s.name,
            "platform": s.platform_id, "category": s.category_id,
            "price_usd": str(s.selling_price_usd),
            "min": s.min_quantity, "max": s.max_quantity,
            "refill": s.supports_refill, "cancel": s.supports_cancel,
            "type": s.service_type, "featured": s.is_featured,
        } for s in items])
    finally:
        db.close()


@bp.route("/service/<int:sid>/price")
def api_price(sid):
    from decimal import Decimal
    db = get_session()
    try:
        s = db.query(Service).filter_by(id=sid, status="active").first()
        if not s:
            return jsonify({"error": "not_found"}), 404
        try:
            qty = int(request.args.get("quantity", s.min_quantity))
        except ValueError:
            qty = s.min_quantity
        total = (s.selling_price_usd * Decimal(qty) / Decimal(1000)
                 ).quantize(Decimal("0.000001"))
        return jsonify({"total_usd": str(total),
                        "per_1000_usd": str(s.selling_price_usd)})
    finally:
        db.close()
