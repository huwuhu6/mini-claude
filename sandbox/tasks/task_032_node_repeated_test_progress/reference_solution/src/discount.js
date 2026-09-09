function total(items) {
  return items.reduce((sum, item) => sum + item.price * item.quantity, 0);
}

function payable(items, code) {
  const subtotal = total(items);
  if (code === "VIP") return subtotal * 0.9;
  return subtotal;
}

module.exports = { total, payable };
