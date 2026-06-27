import asyncio

from app.llm.model_router import ModelRouterError, ModelRoutingMetadata
from app.rag.answer_generator import generate_answer
from app.rag.types import EvidencePack, EvidenceSpan, QuestionAnalysis


class FailingDialogAwareClient:
    async def complete_text_for_dialog(
        self,
        messages: list[dict[str, str]],
        dialog_context: object | None,
    ) -> str:
        del messages, dialog_context
        raise ModelRouterError(
            "Бесплатные модели сейчас не ответили.",
            ModelRoutingMetadata(requested_mode="free", attempted_models=("free-a",)),
        )


def test_answer_generator_surfaces_model_router_user_message() -> None:
    draft = asyncio.run(
        generate_answer(
            QuestionAnalysis(original_question="Как запустить n8n?"),
            EvidencePack(
                items=(
                    EvidenceSpan(
                        evidence_id="ev-1",
                        document_id="doc-1",
                        document_title="Install",
                        text="n8n can be started locally.",
                    ),
                )
            ),
            dialog_context={"user_settings": {"answer_mode": "free"}},
            llm_client=FailingDialogAwareClient(),
        )
    )

    assert draft.text == "Бесплатные модели сейчас не ответили."

