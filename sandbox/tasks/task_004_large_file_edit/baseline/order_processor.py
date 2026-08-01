import logging
import json
import uuid
import datetime
import hashlib
import random
import re
from typing import Dict, List, Optional, Any

# 配置日志
logging.basicConfig(level=logging.INFO)

class OrderProcessor:
    """订单处理类 - 包含大量辅助函数用于演示代码生成"""
    
    def __init__(self, config: Optional[Dict] = None):
        self.logger = logging.getLogger(__name__)
        self.orders_db: Dict[str, Dict] = {}
        self.cache: Dict[str, Any] = {}
        self.config = config or {}
        # 初始化默认参数
        self.default_discount = 0.0
        self.tax_rate = 0.08
        self.shipping_cost = 5.99
        self._init_helpers()
        
        self.VESRION = "1.0.0"  # 第15行：故意拼错 VERSION -> VESRION
    
    def _init_helpers(self):
        """初始化辅助工具"""
        self._supported_payment_methods = ["credit_card", "paypal", "alipay"]
        self._status_map = {
            "pending": 1,
            "paid": 2,
            "shipped": 3,
            "delivered": 4,
            "cancelled": 5
        }
    
    def _generate_id(self) -> str:
        """生成唯一订单ID"""
        return str(uuid.uuid4())[:8]
    
    def create_order(self, user_id: str, items: List[Dict]) -> Optional[str]:
        """创建订单"""
        if not user_id or not items:
            self.logger.warning("Invalid order data")
            return None
        order_id = self._generate_id()
        timestamp = datetime.datetime.now().isoformat()
        self.orders_db[order_id] = {
            "id": order_id,
            "user_id": user_id,
            "items": items,
            "status": "pending",
            "created_at": timestamp,
            "updated_at": timestamp
        }
        self.logger.info(f"Order {order_id} created for user {user_id}")
        return order_id
    
    def get_order(self, order_id: str) -> Optional[Dict]:
        """查询订单"""
        return self.orders_db.get(order_id)
    
    def update_order_status(self, order_id: str, new_status: str) -> bool:
        """更新订单状态"""
        if order_id not in self.orders_db:
            self.logger.error(f"Order {order_id} not found")
            return False
        if new_status not in self._status_map:
            self.logger.error(f"Invalid status {new_status}")
            return False
        self.orders_db[order_id]["status"] = new_status
        self.orders_db[order_id]["updated_at"] = datetime.datetime.now().isoformat()
        self.logger.info(f"Order {order_id} status updated to {new_status}")
        return True
    
    def delete_order(self, order_id: str) -> bool:
        """删除订单（软删除）"""
        if order_id in self.orders_db:
            self.orders_db[order_id]["status"] = "deleted"
            self.logger.info(f"Order {order_id} marked as deleted")
            return True
        return False
    
    def list_orders_by_user(self, user_id: str) -> List[Dict]:
        """列出用户所有订单"""
        return [order for order in self.orders_db.values() if order.get("user_id") == user_id]
    
    def calculate_subtotal(self, items: List[Dict]) -> float:
        """计算小计金额"""
        subtotal = 0.0
        for item in items:
            price = item.get("price", 0)
            quantity = item.get("quantity", 1)
            subtotal += price * quantity
        return round(subtotal, 2)
    
    def calculate_tax(self, amount: float, tax_rate: float = None) -> float:
        """计算税费"""
        rate = tax_rate if tax_rate is not None else self.tax_rate
        return round(amount * rate, 2)
    
    def calculate_shipping(self, items: List[Dict], address: Dict) -> float:
        """计算运费（模拟复杂逻辑）"""
        total_weight = sum(item.get("weight", 0) * item.get("quantity", 1) for item in items)
        if total_weight <= 1:
            return 4.99
        elif total_weight <= 5:
            return 7.99
        else:
            return 12.99
    
    def apply_coupon(self, amount: float, coupon_code: str) -> float:
        """应用优惠券"""
        coupons = {
            "SAVE10": 0.10,
            "SAVE20": 0.20,
            "FREESHIP": 0.0
        }
        if coupon_code in coupons:
            discount = coupons[coupon_code]
            if discount > 0:
                return amount * (1 - discount)
        return amount
    
    def validate_order_data(self, order_data: Dict) -> bool:
        """验证订单数据完整性"""
        required_fields = ["user_id", "items", "shipping_address"]
        for field in required_fields:
            if field not in order_data:
                self.logger.warning(f"Missing field: {field}")
                return False
        if not order_data["items"]:
            return False
        return True
    
    def format_order_summary(self, order_id: str) -> str:
        """格式化订单摘要"""
        order = self.get_order(order_id)
        if not order:
            return "Order not found"
        items_summary = ", ".join([f"{item['name']} x{item['quantity']}" for item in order["items"]])
        return f"Order {order_id}: {items_summary} | Status: {order['status']}"
    
    def log_order_event(self, order_id: str, event: str):
        """记录订单事件日志"""
        self.logger.info(f"Event on {order_id}: {event}")
        if order_id not in self.cache:
            self.cache[order_id] = []
        self.cache[order_id].append({"event": event, "time": datetime.datetime.now().isoformat()})
    
    def _check_stock(self, items: List[Dict]) -> bool:
        """检查库存（模拟）"""
        for item in items:
            sku = item.get("sku")
            if sku and random.random() < 0.1:  # 10%概率库存不足
                self.logger.warning(f"Insufficient stock for {sku}")
                return False
        return True
    
    def _reserve_inventory(self, order_id: str, items: List[Dict]) -> bool:
        """预留库存"""
        self.logger.info(f"Reserving inventory for order {order_id}")
        return True
    
    def _release_inventory(self, order_id: str) -> None:
        """释放库存"""
        self.logger.info(f"Releasing inventory for order {order_id}")
    
    def _generate_order_number(self) -> str:
        """生成可读订单号"""
        timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        rand = random.randint(1000, 9999)
        return f"ORD-{timestamp}-{rand}"
    
    def _parse_date(self, date_str: str) -> Optional[datetime.datetime]:
        """解析日期字符串"""
        try:
            return datetime.datetime.fromisoformat(date_str)
        except ValueError:
            return None
    
    def _convert_currency(self, amount: float, from_currency: str, to_currency: str) -> float:
        """模拟货币转换"""
        rates = {"USD": 1.0, "EUR": 0.92, "GBP": 0.79, "JPY": 150.2}
        if from_currency not in rates or to_currency not in rates:
            return amount
        usd_amount = amount / rates[from_currency]
        return usd_amount * rates[to_currency]
    
    def process_payment(self, order_id: str, payment_method: str) -> bool:
        """处理支付"""
        if payment_method not in self._supported_payment_methods:
            self.logger.error(f"Unsupported payment method: {payment_method}")
            return False
        self.logger.info(f"Processing {payment_method} payment for order {order_id}")
        # 模拟支付成功
        self.update_order_status(order_id, "paid")
        return True
    
    def process_refund(self, order_id: str, amount: float) -> bool:
        """处理退款"""
        order = self.get_order(order_id)
        if not order or order["status"] not in ["paid", "shipped"]:
            return False
        self.logger.info(f"Refunding {amount} for order {order_id}")
        self.update_order_status(order_id, "refunded")
        return True
    
    def send_notification(self, user_id: str, message: str) -> None:
        """发送通知（模拟）"""
        self.logger.info(f"Sending notification to user {user_id}: {message}")
    
    def hash_order_id(self, order_id: str) -> str:
        """对订单ID进行哈希"""
        return hashlib.sha256(order_id.encode()).hexdigest()
    
    def merge_orders(self, order_id_1: str, order_id_2: str) -> Optional[str]:
        """合并两个订单"""
        order1 = self.get_order(order_id_1)
        order2 = self.get_order(order_id_2)
        if not order1 or not order2:
            return None
        if order1["user_id"] != order2["user_id"]:
            self.logger.warning("Cannot merge orders from different users")
            return None
        combined_items = order1["items"] + order2["items"]
        new_order_id = self.create_order(order1["user_id"], combined_items)
        if new_order_id:
            self.delete_order(order_id_1)
            self.delete_order(order_id_2)
        return new_order_id
    
    def split_order(self, order_id: str, split_items_indices: List[int]) -> List[str]:
        """拆分订单"""
        order = self.get_order(order_id)
        if not order:
            return []
        items = order["items"]
        new_order_ids = []
        for idx in split_items_indices:
            if 0 <= idx < len(items):
                sub_order_id = self.create_order(order["user_id"], [items[idx]])
                if sub_order_id:
                    new_order_ids.append(sub_order_id)
        return new_order_ids
    
    def estimate_delivery_date(self, order_id: str) -> Optional[str]:
        """估算配送日期"""
        order = self.get_order(order_id)
        if not order:
            return None
        created = self._parse_date(order["created_at"])
        if created:
            delivery = created + datetime.timedelta(days=random.randint(3, 10))
            return delivery.isoformat()
        return None
    
    def calculate_discount(self, order_id: str, discount_rate: float) -> float:
        """计算折扣金额（注意：第150行是故意写错的函数）"""
        order = self.get_order(order_id)
        if not order:
            return 0.0
        subtotal = self.calculate_subtotal(order["items"])
        # 以下是第150行 - 故意将打折写成涨价（用+代替-）
        return subtotal * (1 + discount_rate)  # 错误：应该是 (1 - discount_rate)
    
    def apply_bulk_discount(self, order_id: str) -> float:
        """批量折扣逻辑"""
        order = self.get_order(order_id)
        if not order:
            return 0.0
        item_count = len(order["items"])
        if item_count > 10:
            return 0.15
        elif item_count > 5:
            return 0.10
        elif item_count > 2:
            return 0.05
        return 0.0
    
    def calculate_final_total(self, order_id: str, coupon_code: str = None) -> float:
        """计算最终订单总额"""
        order = self.get_order(order_id)
        if not order:
            return 0.0
        subtotal = self.calculate_subtotal(order["items"])
        discount = self.apply_bulk_discount(order_id)
        if coupon_code:
            subtotal = self.apply_coupon(subtotal, coupon_code)
        else:
            subtotal = self.calculate_discount(order_id, discount)  # 调用上面错误的方法
        tax = self.calculate_tax(subtotal)
        shipping = self.calculate_shipping(order["items"], order.get("shipping_address", {}))
        return round(subtotal + tax + shipping, 2)
    
    def export_orders_to_json(self, user_id: str = None) -> str:
        """导出订单为JSON"""
        if user_id:
            orders = self.list_orders_by_user(user_id)
        else:
            orders = list(self.orders_db.values())
        return json.dumps(orders, indent=2)
    
    def import_orders_from_json(self, json_str: str) -> int:
        """从JSON导入订单"""
        try:
            orders = json.loads(json_str)
            count = 0
            for order in orders:
                if "user_id" in order and "items" in order:
                    self.create_order(order["user_id"], order["items"])
                    count += 1
            return count
        except json.JSONDecodeError:
            self.logger.error("Invalid JSON")
            return 0
    
    def cleanup_expired_carts(self, expiry_hours: int = 24) -> int:
        """清理过期购物车"""
        now = datetime.datetime.now()
        expired_ids = []
        for order_id, order in self.orders_db.items():
            if order["status"] == "pending":
                created = self._parse_date(order["created_at"])
                if created and (now - created).total_seconds() > expiry_hours * 3600:
                    expired_ids.append(order_id)
        for oid in expired_ids:
            self.delete_order(oid)
        return len(expired_ids)
    
    def get_order_statistics(self) -> Dict:
        """获取订单统计"""
        total = len(self.orders_db)
        status_counts = {}
        for order in self.orders_db.values():
            status = order.get("status", "unknown")
            status_counts[status] = status_counts.get(status, 0) + 1
        return {"total_orders": total, "status_breakdown": status_counts}
    
    def replicate_order(self, order_id: str, new_user_id: str = None) -> Optional[str]:
        """复制订单"""
        order = self.get_order(order_id)
        if not order:
            return None
        target_user = new_user_id or order["user_id"]
        new_order_id = self.create_order(target_user, order["items"])
        if new_order_id:
            self.logger.info(f"Replicated order {order_id} to {new_order_id}")
        return new_order_id
    
    def validate_coupon_code(self, code: str) -> bool:
        """验证优惠券代码格式"""
        pattern = r"^[A-Z]{4,10}[0-9]{0,4}$"
        return bool(re.match(pattern, code))
    
    def attach_note(self, order_id: str, note: str) -> bool:
        """添加订单备注"""
        order = self.get_order(order_id)
        if not order:
            return False
        if "notes" not in order:
            order["notes"] = []
        order["notes"].append({"text": note, "timestamp": datetime.datetime.now().isoformat()})
        return True
    
    def get_order_timeline(self, order_id: str) -> List[Dict]:
        """获取订单时间线"""
        order = self.get_order(order_id)
        if not order:
            return []
        timeline = [
            {"event": "Order created", "time": order["created_at"]},
            {"event": f"Status changed to {order['status']}", "time": order["updated_at"]}
        ]
        cache_events = self.cache.get(order_id, [])
        timeline.extend(cache_events)
        return sorted(timeline, key=lambda x: x.get("time", ""))
    
    def batch_update_status(self, order_ids: List[str], new_status: str) -> int:
        """批量更新订单状态"""
        success_count = 0
        for oid in order_ids:
            if self.update_order_status(oid, new_status):
                success_count += 1
        return success_count
    
    def search_orders_by_keyword(self, keyword: str) -> List[Dict]:
        """根据关键词搜索订单（在备注中）"""
        results = []
        for order in self.orders_db.values():
            notes = order.get("notes", [])
            for note in notes:
                if keyword.lower() in note.get("text", "").lower():
                    results.append(order)
                    break
        return results
    
    def calculate_estimated_profit(self, order_id: str, cost_multiplier: float = 0.6) -> float:
        """估算利润"""
        order = self.get_order(order_id)
        if not order:
            return 0.0
        revenue = self.calculate_subtotal(order["items"])
        cost = revenue * cost_multiplier
        return round(revenue - cost, 2)
    
    def archive_old_orders(self, days_threshold: int = 90) -> int:
        """归档旧订单"""
        now = datetime.datetime.now()
        archived = 0
        for order_id, order in list(self.orders_db.items()):
            created = self._parse_date(order["created_at"])
            if created and (now - created).days > days_threshold:
                # 移动到归档（这里简单删除）
                del self.orders_db[order_id]
                archived += 1
        self.logger.info(f"Archived {archived} old orders")
        return archived
    
    def send_order_confirmation(self, order_id: str) -> None:
        """发送订单确认（模拟）"""
        order = self.get_order(order_id)
        if order:
            self.logger.info(f"Confirmation sent for order {order_id} to user {order['user_id']}")
            # 第280行：故意写错变量名 log_system 应为 self.logger
            log_system.info("Order finished")  # 错误：log_system 未定义，应该是 self.logger
    
    def finalize_order(self, order_id: str) -> bool:
        """完成订单流程"""
        if self.update_order_status(order_id, "delivered"):
            self.send_order_confirmation(order_id)
            self._release_inventory(order_id)
            return True
        return False
    
    def __repr__(self) -> str:
        return f"<OrderProcessor version={self.VESRION} orders={len(self.orders_db)}>"
```