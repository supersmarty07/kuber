from nepal_retail.voice.contracts import parse_order


def preview_order(payload, catalogue):
	order = parse_order(payload)
	lines = []
	for line in order.items:
		candidates = catalogue.search(line.description)
		lines.append(
			{
				"description": line.description,
				"quantity": str(line.quantity),
				"requested_uom": line.uom,
				"candidates": candidates,
				"requires_selection": True,
			}
		)
	discount = None
	if order.discount:
		discount = {
			"amount": str(order.discount.amount),
			"kind": order.discount.kind,
			"scope": order.discount.scope,
			"requires_authorization": True,
		}
	return {
		"status": "review_required",
		"items": lines,
		"customer_query": order.customer,
		"discount_request": discount,
		"can_submit": False,
	}
