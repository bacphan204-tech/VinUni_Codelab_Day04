"""
Lab #4: System Prompt Engineering & Tool Calling Engine
Học viên hoàn thiện các mục TODO để hoàn thành bài lab.

Kiến trúc:
  - ChatbotBaseline: LLM thuần, không dùng tool → quan sát hallucination.
  - ToolCallingAgent: Agent dùng System Prompt + 2 Tool Schemas.
"""

import json
import re
from typing import Dict, Any, List
from tools import TOOL_DEFINITIONS, TOOL_MAP, search_product_catalog, submit_support_ticket

# ═══════════════════════════════════════════════════════════════════════════
# TODO 1: Thiết kế SYSTEM PROMPT cấp sản xuất
# Yêu cầu: Phải chứa Persona, Core Rules, Operational Boundaries, Output Contract.
# ═══════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """
Bạn là VinAssistant, trợ lý tư vấn sản phẩm và dịch vụ thuộc hệ sinh thái Vingroup.

## PERSONA
- Giao tiếp bằng tiếng Việt, chuyên nghiệp, thân thiện và súc tích.
- Hỏi lại khi thiếu thông tin cần thiết; không tự đoán tên khách hàng hay nội dung sự cố.

## AVAILABLE TOOLS
- search_product_catalog(category, max_price): tra cứu sản phẩm, dịch vụ theo danh mục và giá tối đa.
- submit_support_ticket(customer_name, issue_description, priority): ghi nhận yêu cầu hỗ trợ.

## CORE RULES
1. Không bịa tên sản phẩm, giá, tình trạng hay chính sách.
2. Khi người dùng yêu cầu tìm sản phẩm hoặc dịch vụ, phải tra cứu catalog.
3. Khi người dùng muốn ghi nhận yêu cầu hỗ trợ, phải tạo ticket nếu đã có tên và mô tả vấn đề.
4. Chỉ xác nhận ticket đã tạo khi nhận được mã ticket từ tool.
5. Nếu không có kết quả tra cứu, hãy nói rõ là không tìm thấy.
6. Nếu không đủ dữ liệu để trả lời chính xác, hãy nói rõ giới hạn đó thay vì suy đoán.

## OPERATIONAL BOUNDARIES
- Chỉ hỗ trợ sản phẩm, dịch vụ và yêu cầu thuộc hệ sinh thái Vingroup.
- Với câu hỏi ngoài phạm vi, từ chối hoặc hướng người dùng về đúng phạm vi một cách lịch sự.

## OUTPUT CONTRACT
- Luồng xử lý: Thought -> Action (nếu cần) -> Observation -> Final Answer.
- Không hiển thị suy luận nội bộ; chỉ trả lời kết quả cuối cùng.
- Nêu rõ kết quả tra cứu, mã ticket đã tạo, hoặc thông tin còn thiếu.
"""


# ═══════════════════════════════════════════════════════════════════════════
# CLASS: ChatbotBaseline
# ═══════════════════════════════════════════════════════════════════════════

class ChatbotBaseline:
    """Baseline LLM Chatbot — Không sử dụng Tool Calling hay ReAct Loop."""

    def query(self, user_input: str) -> Dict[str, Any]:
        # TODO 2: Trả về câu trả lời tĩnh (mock) hoặc gọi Gemini API 1 lượt (không dùng tool)
        # Mục tiêu: Quan sát hiện tượng bịa thông tin (hallucination)
        return {
            "answer": f"[Chatbot Baseline] Trả lời cho: {user_input}",
            "tool_calls": [],
            "status": "success",
            "mode": "mock_baseline"
        }


# ═══════════════════════════════════════════════════════════════════════════
# CLASS: ToolCallingAgent
# ═══════════════════════════════════════════════════════════════════════════

class ToolCallingAgent:
    """Agent với System Prompt Engineering & Tool Calling."""

    def __init__(self, max_iterations: int = 5):
        self.max_iterations = max_iterations
        self.trace: List[Dict[str, Any]] = []

    def run(self, user_input: str) -> Dict[str, Any]:
        """Điểm vào chính — chạy Agent Loop."""
        self.trace = []

        # TODO 3: Phân tích intent từ user_input
        #   - Xác định cần gọi tool nào (catalog? ticket? cả hai? FAQ?)
        #   - Gợi ý: Dùng keyword matching hoặc regex


        lowered = user_input.lower()
        mentions_product = any(
            word in lowered
            for word in ("xe điện", "xe vinfast", "vinfast", "vinpearl", "resort", "du lịch")
        )
        asks_to_browse = any(
            word in lowered
            for word in ("muốn xem", "cho tôi xem", "tìm", "liệt kê", "sản phẩm",
                         "giá dưới", "giá tối đa", "có xe", "có resort", "có gói")
        )
        needs_catalog = mentions_product and asks_to_browse

        ticket_intent = any(
            word in lowered
            for word in ("ghi nhận", "tạo ticket", "yêu cầu hỗ trợ", "bị lỗi",
                         "sự cố", "phản hồi", "khiếu nại")
        )
        needs_ticket = ticket_intent
        is_faq = not needs_catalog and not needs_ticket

        intents = {
            "needs_catalog": needs_catalog,
            "needs_ticket": needs_ticket,
            "is_faq": is_faq
        }
        self.trace.append({"step": "intent_detection", "intents": intents})
        planned_tools = []
        needs_input = False

        if needs_catalog:
            category = (
                "du_lich"
                if any(word in lowered for word in ("vinpearl", "resort", "du lịch"))
                else "xe_dien"
            )
            arguments = {"category": category}
            price_match = re.search(
                r"(?:dưới|tối đa|không quá)\s*([\d.,]+)\s*"
                r"(tỷ|triệu|tr|nghìn|ngàn)?",
                lowered
            )
            if price_match:
                amount_text, unit = price_match.groups()
                if unit and re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", amount_text):
                    amount = float(re.sub(r"[.,]", "", amount_text))
                else:
                    amount = float(amount_text.replace(",", "."))
                multiplier = {
                    "tỷ": 1_000_000_000,
                    "triệu": 1_000_000,
                    "tr": 1_000_000,
                    "nghìn": 1_000,
                    "ngàn": 1_000
                }.get(unit, 1)
                arguments["max_price"] = int(amount * multiplier)
            planned_tools.append(("search_product_catalog", arguments))

        if needs_ticket:
            name_match = re.search(
                r"(?:tên tôi là|tôi tên là|tôi tên)\s*([^,.;:!?]+)",
                user_input,
                re.IGNORECASE
            )
            if name_match:
                customer_name = name_match.group(1).strip()
                issue = user_input[name_match.end():].strip(" ,:;")
                issue = re.split(r"[.!?]", issue, maxsplit=1)[0]
                issue = re.split(
                    r",\s*(?:mức độ|độ ưu tiên)",
                    issue,
                    maxsplit=1,
                    flags=re.IGNORECASE
                )[0].strip(" ,:;")
                if issue:
                    priority = (
                        "high"
                        if any(word in lowered for word in
                               ("nghiêm trọng", "xử lý gấp", "khẩn cấp", "cấp bách"))
                        else "low"
                        if any(word in lowered for word in
                               ("mức độ thấp", "ưu tiên thấp", "không gấp"))
                        else "medium"
                    )
                    planned_tools.append((
                        "submit_support_ticket",
                        {
                            "customer_name": customer_name,
                            "issue_description": issue,
                            "priority": priority
                        }
                    ))
                else:
                    needs_input = True
            else:
                needs_input = True

        iterations = max(1, len(planned_tools))
        if iterations > self.max_iterations:
            answer = "Lỗi: Vượt quá số bước tối đa."
            self.trace.append({"step": "max_iterations", "answer": answer})
            return {
                "answer": answer,
                "trace": self.trace,
                "iterations": self.max_iterations,
                "status": "max_iterations_reached"
            }

        observations = {}
        for step, (tool_name, arguments) in enumerate(planned_tools, start=1):
            result = TOOL_MAP[tool_name](**arguments)
            observations[tool_name] = result
            self.trace.append({
                "step": "tool_call",
                "iteration": step,
                "tool": tool_name,
                "arguments": arguments,
                "observation": result
            })

        answers = []
        if needs_catalog:
            products = observations["search_product_catalog"]
            if not products or (len(products) == 1 and "error" in products[0]):
                answers.append("Rất tiếc, không tìm thấy sản phẩm phù hợp.")
            else:
                product_text = "; ".join(
                    f"{product['name']} — {product['price_vnd']:,} VNĐ"
                    for product in products
                )
                answers.append(f"Sản phẩm phù hợp: {product_text}.")

        if "submit_support_ticket" in observations:
            ticket = observations["submit_support_ticket"]
            answers.append(
                f"Đã ghi nhận yêu cầu của {ticket['customer_name']}. "
                f"Mã ticket: {ticket['ticket_id']}."
            )
        elif needs_input:
            answers.append("Vui lòng cung cấp tên và mô tả vấn đề để tạo ticket.")

        if is_faq:
            if "bảo hành" in lowered and ("pin" in lowered or "xe điện" in lowered):
                answers.append(
                    "Theo dữ liệu sản phẩm hiện có, pin VinFast VF 5 Plus được "
                    "bảo hành 10 năm. Chính sách có thể khác theo dòng xe; "
                    "hãy xác nhận với VinFast để biết điều kiện áp dụng."
                )
            else:
                answers.append("Mình chưa có đủ dữ liệu để xác nhận thông tin này.")

        answer = " ".join(answers)
        self.trace.append({"step": "final_answer", "answer": answer})
        return {
            "answer": answer,
            "trace": self.trace,
            "iterations": iterations,
            "status": "needs_input" if needs_input else "completed"
        }



# ═══════════════════════════════════════════════════════════════════════════
# MAIN — Chạy thử nhanh
# ═══════════════════════════════════════════════════════════════════════════

def main():
    user_query = "Tôi muốn xem xe điện VinFast giá dưới 600 triệu."

    print("=== RUNNING CHATBOT BASELINE ===")
    chatbot = ChatbotBaseline()
    print(chatbot.query(user_query))

    print("\n=== RUNNING TOOL CALLING AGENT ===")
    agent = ToolCallingAgent(max_iterations=5)
    result = agent.run(user_query)
    print("Result:", result["answer"])
    print("Trace Log:", json.dumps(agent.trace, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
