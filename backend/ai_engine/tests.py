import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import JobRole
from audit.models import AuditLog
from quiz.models import Question
from sops.models import SOPChunk, SOPDocument

from . import services, tasks
from .services import select_relevant_chunks


class GenerateQuizTests(APITestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(username="anjali", password="demo12345", is_staff=True)
        self.learner = get_user_model().objects.create_user(username="rohit", password="demo12345")
        self.client.force_authenticate(user=self.admin)

        self.role = JobRole.objects.create(name="Production Operator", department="Production")
        self.sop = SOPDocument.objects.create(
            title="Cleanroom Entry and Gowning",
            sop_code="SOP-900",
            version="v1.0",
            department="Production",
            file="sops/sop-900.txt",
            status="processed",
        )
        SOPChunk.objects.create(
            sop=self.sop,
            section_title="Auto chunk 1",
            chunk_text=(
                "Personnel must don garments in the following strict order: hair cover, face mask, "
                "sterile coverall, safety goggles, and sterile gloves last. Sterile gloves are always "
                "donned last to prevent contamination of the outer glove surface during earlier steps. "
                "A single gowning cycle inside the Grade B area is limited to a maximum of four hours "
                "before personnel must exit and re-gown regardless of task completion status."
            ),
        )

    def test_generate_rejected_for_plain_learner(self):
        """RBAC: triggering AI generation is an Admin action, not available to a plain learner."""
        self.client.force_authenticate(user=self.learner)
        response = self.client.post(
            "/api/ai_engine/generate/",
            {"sop": self.sop.id, "job_role": self.role.id, "count": 1},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_generate_requires_sop_and_job_role(self):
        response = self.client.post("/api/ai_engine/generate/", {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_generate_rejects_sop_with_no_chunks(self):
        empty_sop = SOPDocument.objects.create(
            title="No Chunks Yet", sop_code="SOP-901", version="v1.0", department="Production",
            file="sops/sop-901.txt", status="uploaded",
        )
        response = self.client.post(
            "/api/ai_engine/generate/",
            {"sop": empty_sop.id, "job_role": self.role.id, "count": 2},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @mock.patch.dict("os.environ", {"NVIDIA_API_KEY": ""})
    def test_generate_falls_back_to_mock_and_creates_draft_questions(self):
        response = self.client.post(
            "/api/ai_engine/generate/",
            {"sop": self.sop.id, "job_role": self.role.id, "count": 3},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["source"], "mock")
        self.assertEqual(len(response.data["questions"]), 3)

        questions = Question.objects.filter(sop=self.sop, job_role=self.role)
        self.assertEqual(questions.count(), 3)
        for question in questions:
            self.assertEqual(question.status, "draft")
            self.assertEqual(question.options.filter(is_correct=True).count(), 1)

    @mock.patch.dict("os.environ", {"NVIDIA_API_KEY": ""})
    def test_generate_skips_duplicates_on_a_repeat_run(self):
        """Running generate twice for the same SOP+role should not create the same
        question-and-correct-answer pair twice, even though the mock generator's question
        stem is identical across calls (only the correct answer differs per fact tested)."""
        first = self.client.post(
            "/api/ai_engine/generate/",
            {"sop": self.sop.id, "job_role": self.role.id, "count": 3},
            format="json",
        )
        self.assertEqual(len(first.data["questions"]), 3)
        self.assertEqual(first.data["skipped_duplicates"], 0)

        second = self.client.post(
            "/api/ai_engine/generate/",
            {"sop": self.sop.id, "job_role": self.role.id, "count": 3},
            format="json",
        )
        self.assertEqual(len(second.data["questions"]), 0)
        self.assertEqual(second.data["skipped_duplicates"], 3)
        self.assertEqual(Question.objects.filter(sop=self.sop, job_role=self.role).count(), 3)


@mock.patch("ai_engine.services.time.sleep", lambda *_: None)
@mock.patch.dict("os.environ", {"NVIDIA_API_KEY": "test-key-not-a-real-credential"})
class GenerationGroundingTests(APITestCase):
    """The adaptive claim rests entirely on Question.source_chunk: an answer is evidence
    about a *passage*, not just a document. Every adaptive test builds its fixtures by hand
    (`Question.objects.create(..., source_chunk=chunk)`), so they all verify the *consumer*
    of that link. Nothing verified the *producer* -- generation could stop populating
    source_chunk and every adaptive test would still pass while chunk-level mastery
    silently died, with all questions falling into the 'unlinked' bucket."""

    def setUp(self):
        self.admin = get_user_model().objects.create_user(
            username="anjali", password="demo12345", is_staff=True
        )
        self.role = JobRole.objects.create(name="Production Operator", department="Production")
        self.sop = SOPDocument.objects.create(
            title="Quality Management", sop_code="SOP-980", version="v1.0",
            department="Production", file="sops/sop-980.txt", status="processed",
        )
        self.gmp = SOPChunk.objects.create(
            sop=self.sop, section_title="GMP", chunk_text="Equipment must be verified as clean."
        )
        self.capa = SOPChunk.objects.create(
            sop=self.sop, section_title="CAPA",
            chunk_text="A deviation must be recorded within one working day.",
        )
        self.client.force_authenticate(user=self.admin)

    def _payload(self, stem, answer):
        return json.dumps([{
            "question_text": stem, "difficulty": "medium",
            "options": [answer, "Wrong one", "Wrong two", "Wrong three"],
            "correct_option_index": 0, "explanation": "Because the SOP says so.",
            "confidence": 0.9,
        }])

    @mock.patch("ai_engine.services.OpenAI")
    def test_generation_links_questions_to_their_source_chunk(self, mock_openai):
        """Every generated question must carry the chunk it was generated from."""
        call = mock_openai.return_value.chat.completions.create
        call.side_effect = [
            mock.Mock(choices=[mock.Mock(message=mock.Mock(
                content=self._payload("When must equipment be verified clean?", "Before any batch")
            ))]),
            mock.Mock(choices=[mock.Mock(message=mock.Mock(
                content=self._payload("How soon must a deviation be recorded?", "Within one working day")
            ))]),
        ]

        response = self.client.post(
            "/api/ai_engine/generate/",
            {"sop": self.sop.id, "job_role": self.role.id, "count": 2},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        questions = Question.objects.filter(sop=self.sop, job_role=self.role)
        self.assertEqual(questions.count(), 2)
        # The critical assertion: not one question may be orphaned from its source.
        self.assertFalse(questions.filter(source_chunk__isnull=True).exists())
        # And each must point at a chunk of *this* SOP.
        self.assertEqual(
            set(questions.values_list("source_chunk_id", flat=True)),
            {self.gmp.id, self.capa.id},
        )

    @mock.patch("ai_engine.services.OpenAI")
    def test_offline_fallback_also_links_the_source_chunk(self, mock_openai):
        """Grounding must survive the degraded path too -- otherwise an API outage would
        silently produce a batch of questions invisible to chunk-level mastery."""
        mock_openai.return_value.chat.completions.create.side_effect = Exception("503 provider error")

        self.client.post(
            "/api/ai_engine/generate/",
            {"sop": self.sop.id, "job_role": self.role.id, "count": 2},
            format="json",
        )
        questions = Question.objects.filter(sop=self.sop, job_role=self.role)
        self.assertTrue(questions.exists())
        self.assertFalse(questions.filter(source_chunk__isnull=True).exists())
        self.assertTrue(all(q.generation_source == "mock" for q in questions))

    @mock.patch("ai_engine.services.OpenAI")
    def test_source_chunk_matches_the_text_the_question_was_generated_from(self, mock_openai):
        """Stronger than 'a chunk is set': the linked chunk must be the one whose text was
        actually sent to the model, so the SME's source passage is the real provenance."""
        seen_prompts = []

        def capture(**kwargs):
            seen_prompts.append(kwargs["messages"][-1]["content"])
            return mock.Mock(choices=[mock.Mock(message=mock.Mock(
                content=self._payload(f"Question {len(seen_prompts)}?", f"Answer {len(seen_prompts)}")
            ))])

        mock_openai.return_value.chat.completions.create.side_effect = capture

        self.client.post(
            "/api/ai_engine/generate/",
            {"sop": self.sop.id, "job_role": self.role.id, "count": 2},
            format="json",
        )
        for question in Question.objects.filter(sop=self.sop).select_related("source_chunk"):
            index = int(question.question_text.split()[1].rstrip("?")) - 1
            self.assertIn(question.source_chunk.chunk_text, seen_prompts[index])


class NearDuplicateDetectionTests(SimpleTestCase):
    """Exact-signature matching only catches a verbatim repeat; the model rewords instead.
    These pin both the catches and, just as importantly, the non-catches -- an
    over-eager duplicate filter would silently discard legitimate coverage."""

    def _dupe(self, q1, a1, q2, a2):
        return tasks.is_near_duplicate(q2, a2, [(q1, a1)])

    def test_exact_duplicate_is_caught(self):
        self.assertTrue(self._dupe(
            "What must be done before batch release?", "Verify cleaning",
            "What must be done before batch release?", "Verify cleaning",
        ))

    def test_reworded_duplicate_is_caught(self):
        """The case exact matching misses entirely."""
        self.assertTrue(self._dupe(
            "What must be done before batch release?", "Cleaning must be verified",
            "Prior to batch release, what is required?", "Cleaning must be verified",
        ))

    def test_genuinely_different_question_is_not_caught(self):
        self.assertFalse(self._dupe(
            "How soon must a deviation be recorded?", "Within one working day",
            "What order should gowning follow?", "Gloves are donned last",
        ))

    def test_same_topic_different_learning_objective_is_not_caught(self):
        """Both are about CAPA, but they test different facts and have different answers --
        discarding the second would lose real coverage of the section."""
        self.assertFalse(self._dupe(
            "How soon must a deviation be recorded?", "Within one working day",
            "What must precede approval of a corrective action?",
            "A documented root cause investigation",
        ))

    def test_same_stem_but_different_answer_is_not_caught(self):
        """Requiring both stem *and* answer similarity is what keeps false positives down."""
        self.assertFalse(self._dupe(
            "What is required before batch release?", "Cleaning verification",
            "What is required before batch release?", "Supervisor sign-off",
        ))

    def test_similarity_is_symmetric_and_bounded(self):
        a, b = "Cleaning must be verified", "Verification of cleaning is required"
        self.assertAlmostEqual(tasks.token_similarity(a, b), tasks.token_similarity(b, a))
        self.assertEqual(tasks.token_similarity(a, a), 1.0)
        self.assertEqual(tasks.token_similarity("", "anything"), 0.0)


class LLMErrorClassificationTests(SimpleTestCase):
    """The fallback contract swallows every provider failure so the pipeline degrades
    instead of breaking -- which made an expired key, an exhausted quota and a network blip
    indistinguishable in production. These are the categories an operator must tell apart."""

    def test_classifies_rate_limit(self):
        self.assertEqual(services.classify_llm_error(Exception("429 rate limit exceeded")), "rate_limit")

    def test_classifies_authentication_failure(self):
        self.assertEqual(
            services.classify_llm_error(Exception("401 Unauthorized: invalid api key")),
            "authentication_failure",
        )

    def test_classifies_timeout(self):
        self.assertEqual(services.classify_llm_error(TimeoutError("request timed out")), "timeout")

    def test_classifies_invalid_model_output(self):
        error = json.JSONDecodeError("Expecting value", "not json", 0)
        self.assertEqual(services.classify_llm_error(error), "invalid_model_output")

    def test_classifies_validation_failure(self):
        self.assertEqual(
            services.classify_llm_error(ValueError("AI response contained no usable questions")),
            "validation_failure",
        )

    def test_unrecognised_error_is_not_misreported(self):
        self.assertEqual(services.classify_llm_error(Exception("something odd")), "unknown")


@mock.patch("ai_engine.services.time.sleep", lambda *_: None)  # keep the backoff instant
@mock.patch.dict("os.environ", {"NVIDIA_API_KEY": "test-key-not-a-real-credential"})
class LiveLLMPathTests(SimpleTestCase):
    """Previously every AI test forced NVIDIA_API_KEY="" , which returns before the HTTP
    client is ever constructed -- so the retry loop, the markdown-fence stripping, the JSON
    parsing and the draft validation had no coverage at all. These mock the provider rather
    than the key, exercising the code that actually runs against a live API."""

    def _client(self, mock_openai):
        return mock_openai.return_value.chat.completions.create

    def _valid_payload(self):
        return json.dumps([
            {
                "question_text": "When is calibration required?",
                "difficulty": "medium",
                "options": ["Annually", "Never", "Weekly", "Only after moving it"],
                "correct_option_index": 0,
                "explanation": "The SOP requires annual calibration.",
                "confidence": 0.8,
            }
        ])

    @mock.patch("ai_engine.services.OpenAI")
    def test_successful_live_call_is_parsed_into_drafts(self, mock_openai):
        self._client(mock_openai).return_value = mock.Mock(
            choices=[mock.Mock(message=mock.Mock(content=self._valid_payload()))]
        )
        drafts, source = services.generate_questions("QC Chemist", "Calibrate annually.", 1)
        self.assertEqual(source, "nvidia_nim")
        self.assertEqual(drafts[0]["correct_option_index"], 0)
        self.assertEqual(drafts[0]["confidence"], 0.8)

    @mock.patch("ai_engine.services.OpenAI")
    def test_markdown_fenced_json_is_still_parsed(self, mock_openai):
        """Instruct-tuned models routinely wrap JSON in ```json fences despite being told
        not to."""
        fenced = f"```json\n{self._valid_payload()}\n```"
        self._client(mock_openai).return_value = mock.Mock(
            choices=[mock.Mock(message=mock.Mock(content=fenced))]
        )
        drafts, source = services.generate_questions("QC Chemist", "Calibrate annually.", 1)
        self.assertEqual(source, "nvidia_nim")
        self.assertEqual(len(drafts), 1)

    @mock.patch("ai_engine.services.OpenAI")
    def test_provider_error_retries_three_times_then_falls_back(self, mock_openai):
        call = self._client(mock_openai)
        call.side_effect = Exception("503 provider error")
        drafts, source = services.generate_questions("QC Chemist", "Calibrate annually.", 1)
        self.assertEqual(call.call_count, services.NVIDIA_NIM_MAX_ATTEMPTS)
        self.assertEqual(source, "mock")
        self.assertTrue(drafts)  # degraded, never empty

    @mock.patch("ai_engine.services.OpenAI")
    def test_a_transient_failure_is_recovered_by_the_retry(self, mock_openai):
        """The point of retrying: one blip must not drop the whole run to offline content."""
        call = self._client(mock_openai)
        call.side_effect = [
            Exception("503 provider error"),
            mock.Mock(choices=[mock.Mock(message=mock.Mock(content=self._valid_payload()))]),
        ]
        _drafts, source = services.generate_questions("QC Chemist", "Calibrate annually.", 1)
        self.assertEqual(call.call_count, 2)
        self.assertEqual(source, "nvidia_nim")

    @mock.patch("ai_engine.services.OpenAI")
    def test_malformed_json_never_persists_and_falls_back(self, mock_openai):
        self._client(mock_openai).return_value = mock.Mock(
            choices=[mock.Mock(message=mock.Mock(content="not json at all"))]
        )
        _drafts, source = services.generate_questions("QC Chemist", "Calibrate annually.", 1)
        self.assertEqual(source, "mock")

    @mock.patch("ai_engine.services.OpenAI")
    def test_structurally_invalid_drafts_are_rejected(self, mock_openai):
        """Valid JSON, but every item is missing required keys -- must not reach the DB."""
        self._client(mock_openai).return_value = mock.Mock(
            choices=[mock.Mock(message=mock.Mock(content=json.dumps([{"question_text": "Only a stem?"}])))]
        )
        _drafts, source = services.generate_questions("QC Chemist", "Calibrate annually.", 1)
        self.assertEqual(source, "mock")

    @mock.patch("ai_engine.services.OpenAI")
    def test_confidence_is_clamped_into_range(self, mock_openai):
        payload = json.loads(self._valid_payload())
        payload[0]["confidence"] = 4.2
        self._client(mock_openai).return_value = mock.Mock(
            choices=[mock.Mock(message=mock.Mock(content=json.dumps(payload)))]
        )
        drafts, _source = services.generate_questions("QC Chemist", "Calibrate annually.", 1)
        self.assertEqual(drafts[0]["confidence"], 1.0)

    @mock.patch("ai_engine.services.OpenAI")
    def test_sop_chat_falls_back_after_exhausting_retries(self, mock_openai):
        self._client(mock_openai).side_effect = TimeoutError("timed out")
        chunk = FakeChunk("Gowning Sequence", "Sterile gloves are donned last.")
        answer, sections, source = services.answer_sop_question("SOP-1", "When do gloves go on?", [chunk])
        self.assertEqual(source, "mock")
        self.assertIn("Gowning Sequence", answer)
        self.assertEqual(sections, ["Gowning Sequence"])


class FakeChunk:
    """A minimal chunk-like stand-in (section_title/chunk_text only) so
    select_relevant_chunks can be unit-tested without touching the database."""

    def __init__(self, section_title, chunk_text):
        self.section_title = section_title
        self.chunk_text = chunk_text


class SelectRelevantChunksTests(SimpleTestCase):
    def test_ranks_the_chunk_with_the_most_keyword_overlap_first(self):
        chunks = [
            FakeChunk("Gowning Sequence", "Personnel must don hair cover, face mask, and sterile gloves last."),
            FakeChunk("HPLC Calibration", "The HPLC system must be calibrated annually per the manufacturer manual."),
        ]
        selected = select_relevant_chunks("What is the correct order for donning sterile gloves?", chunks, max_chunks=1)
        self.assertEqual(selected[0].section_title, "Gowning Sequence")

    def test_falls_back_to_document_order_when_nothing_matches(self):
        chunks = [FakeChunk("Section 1", "Alpha beta gamma."), FakeChunk("Section 2", "Delta epsilon zeta.")]
        selected = select_relevant_chunks("completely unrelated query xyz", chunks, max_chunks=2)
        self.assertEqual([c.section_title for c in selected], ["Section 1", "Section 2"])


class SopChatTests(APITestCase):
    def setUp(self):
        self.learner = get_user_model().objects.create_user(username="rohit", password="demo12345")
        self.sop = SOPDocument.objects.create(
            title="Cleanroom Entry and Gowning", sop_code="SOP-900", version="v1.0", department="Production",
            file="sops/sop-900.txt", status="processed",
        )
        SOPChunk.objects.create(
            sop=self.sop,
            section_title="Gowning Sequence",
            chunk_text=(
                "Personnel must don garments in the following strict order: hair cover, face mask, "
                "sterile coverall, safety goggles, and sterile gloves last, to prevent contamination "
                "of the outer glove surface during earlier gowning steps."
            ),
        )
        self.client.force_authenticate(user=self.learner)

    def test_chat_requires_authentication(self):
        self.client.force_authenticate(user=None)
        response = self.client.post("/api/ai_engine/sop-chat/", {"sop": self.sop.id, "question": "Why?"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_chat_requires_sop_and_question(self):
        response = self.client.post("/api/ai_engine/sop-chat/", {"sop": self.sop.id}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_chat_rejects_an_overlong_question(self):
        response = self.client.post(
            "/api/ai_engine/sop-chat/", {"sop": self.sop.id, "question": "why? " * 200}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_chat_rejects_sop_with_no_chunks(self):
        empty_sop = SOPDocument.objects.create(
            title="No Chunks Yet", sop_code="SOP-901", version="v1.0", department="Production",
            file="sops/sop-901.txt", status="uploaded",
        )
        response = self.client.post(
            "/api/ai_engine/sop-chat/", {"sop": empty_sop.id, "question": "Why?"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @mock.patch.dict("os.environ", {"NVIDIA_API_KEY": ""})
    def test_chat_falls_back_to_offline_and_answers_from_sop_text(self):
        response = self.client.post(
            "/api/ai_engine/sop-chat/",
            {"sop": self.sop.id, "question": "What order should gloves be donned in?"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["source"], "mock")
        self.assertIn("Gowning Sequence", response.data["answer"])
        self.assertEqual(response.data["sections_used"], ["Gowning Sequence"])

    @mock.patch.dict("os.environ", {"NVIDIA_API_KEY": ""})
    def test_chat_writes_an_audit_log_entry(self):
        self.client.post(
            "/api/ai_engine/sop-chat/", {"sop": self.sop.id, "question": "What order should gloves be donned in?"},
            format="json",
        )
        entry = AuditLog.objects.get(action="sop_chat_query")
        self.assertEqual(entry.user, self.learner)
        self.assertEqual(entry.object_id, self.sop.id)
