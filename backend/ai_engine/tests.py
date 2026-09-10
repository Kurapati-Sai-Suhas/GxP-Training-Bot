import json
import os
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import JobRole
from audit.models import AuditLog
from quiz.models import Question
from sops.models import SOPChunk, SOPDocument

from . import retrieval_evaluation, services, tasks
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

    def test_classifies_retired_model_from_410(self):
        """A retired model is permanent; a transient blip is not. Conflating them is how
        both of this project's models stayed dead for two weeks unnoticed.

        NVIDIA returns HTTP 410 Gone with an 'end of life' detail. That previously fell
        through every branch to 'unknown', so the log line was indistinguishable from a
        network hiccup and no operator had reason to act."""
        real_message = (
            "Error code: 410 - {'type': 'about:blank', 'title': 'Gone', 'status': 410, "
            "'detail': \"The model 'meta/llama-3.1-8b-instruct' has reached its end of "
            "life on 2026-08-26T09:00:00Z and is no longer available.\"}"
        )
        self.assertEqual(services.classify_llm_error(Exception(real_message)), "model_retired")

    def test_retired_is_distinct_from_model_not_found(self):
        """404 means 'not entitled / wrong id' -- a config error that may be fixable in
        place. 410 means 'this will never work again'. Different remedies, different
        categories, so an alert can route them differently."""
        self.assertEqual(
            services.classify_llm_error(Exception("Error code: 404 - Not found for account")),
            "model_not_found",
        )
        self.assertEqual(
            services.classify_llm_error(Exception("model has reached its end of life")),
            "model_retired",
        )


class ModelConfigurationTests(SimpleTestCase):
    """Model ids must be configuration, not code.

    Both models were hardcoded constants. When NVIDIA retired them, recovery required a
    code edit, review, rebuild and redeploy -- for what is a one-line config change.
    Resolution happens at CALL time so a running worker picks up a new value."""

    def test_chat_model_defaults_when_unset(self):
        with mock.patch.dict("os.environ", {}, clear=False):
            os.environ.pop("NVIDIA_NIM_MODEL", None)
            self.assertEqual(services.nim_model(), services.DEFAULT_NVIDIA_NIM_MODEL)

    def test_chat_model_is_overridable_without_a_code_change(self):
        with mock.patch.dict("os.environ", {"NVIDIA_NIM_MODEL": "vendor/replacement-v2"}):
            self.assertEqual(services.nim_model(), "vendor/replacement-v2")

    def test_embedding_model_is_overridable_without_a_code_change(self):
        from sops import services as sop_services

        with mock.patch.dict("os.environ", {"NVIDIA_EMBED_MODEL": "vendor/embed-v9"}):
            self.assertEqual(sop_services.embed_model(), "vendor/embed-v9")

    def test_default_chat_model_is_not_a_retired_model(self):
        """Guards the specific regression that caused this work: shipping a default that
        the provider has already retired."""
        retired = {"meta/llama-3.1-8b-instruct", "nvidia/nv-embedqa-e5-v5"}
        self.assertNotIn(services.DEFAULT_NVIDIA_NIM_MODEL, retired)

    def test_resolution_is_at_call_time_not_import_time(self):
        """Import-time resolution would mean a worker had to restart to pick up a rotation."""
        with mock.patch.dict("os.environ", {"NVIDIA_NIM_MODEL": "first/model"}):
            self.assertEqual(services.nim_model(), "first/model")
        with mock.patch.dict("os.environ", {"NVIDIA_NIM_MODEL": "second/model"}):
            self.assertEqual(services.nim_model(), "second/model")


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

    def test_production_default_ranker_is_the_measured_winner(self):
        """BM25 was implemented and evaluated against this gold set, and LOST on every
        configuration tested (MRR 0.9048-0.9167 vs overlap's 0.9286). This pins the decision
        so a later 'BM25 is standard practice' edit cannot silently regress retrieval."""
        self.assertEqual(services.DEFAULT_RANKER, "overlap")

    def test_both_rankers_remain_callable_for_re_evaluation(self):
        """The rejected ranker is kept so the comparison can be re-run when the corpus grows
        past the scale at which IDF becomes a meaningful statistic."""
        chunks = [
            FakeChunk("Gowning", "Personnel must don hair cover, face mask and sterile gloves last."),
            FakeChunk("HPLC", "The HPLC system must be calibrated annually per the manual."),
        ]
        for ranker in ("overlap", "bm25"):
            with self.subTest(ranker=ranker):
                selected = select_relevant_chunks("donning sterile gloves order", chunks,
                                                  max_chunks=2, ranker=ranker)
                self.assertEqual(len(selected), 2)

    def test_bm25_length_normalisation_is_active(self):
        """Pins the MECHANISM behind the rejection, at a scale a unit test can honestly assert.

        The corpus-level effect that sank BM25 (a 6-token title chunk scoring 2.885 against
        the correct 31-token chunk's 1.472) depends on document-frequency statistics across
        a 5-chunk corpus, and cannot be faithfully reproduced with a two-chunk fixture --
        IDF behaves quite differently at N=2. That evidence lives in the measurement script
        (scratchpad/bm25_corrected.py) and the decision comment beside DEFAULT_RANKER.

        What IS honestly assertable here is that length normalisation is switched on and
        does what b is supposed to do: of two chunks matching the same single term, the
        shorter scores higher. That is the property which, at corpus scale, elevated the
        degenerate chunks."""
        short = FakeChunk("Short", "Gowning required.")
        long = FakeChunk("Long", "Gowning is required and " + "additional procedural text " * 12)
        scores = services._bm25_scores("gowning", [short, long])
        self.assertGreater(scores[0], scores[1],
                           "b>0 must favour the shorter chunk for an equal term match")
        self.assertGreater(services.BM25_B, 0, "length normalisation must be enabled")


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


class RetrievalGoldSetTests(SimpleTestCase):
    """The gold set is the measuring instrument. If it drifts, every retrieval number based on
    it silently becomes wrong, so its shape and its honesty markers are pinned here."""

    def setUp(self):
        self.gold = retrieval_evaluation.load_gold_set()

    def test_1_schema(self):
        self.assertIn("version", self.gold)
        self.assertIn("queries", self.gold)
        required = {"id", "query", "sop_code", "relevant_sections", "category", "notes"}
        for case in self.gold["queries"]:
            with self.subTest(query=case.get("id")):
                self.assertTrue(required.issubset(case), f"missing {required - set(case)}")
                self.assertIsInstance(case["relevant_sections"], list)
                self.assertTrue(case["notes"].strip(), "every label needs a stated rationale")

    def test_2_loading_is_deterministic(self):
        self.assertEqual(self.gold, retrieval_evaluation.load_gold_set())

    def test_query_ids_are_unique(self):
        ids = [c["id"] for c in self.gold["queries"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_synthetic_demo_corpus_is_excluded(self):
        """SOP-DEMO is wiped and regenerated by demo_adaptive, so a label keyed to it would
        break on every demo run."""
        self.assertIn("SOP-DEMO", self.gold["corpus"]["excluded"])
        for case in self.gold["queries"]:
            self.assertNotEqual(case["sop_code"], "SOP-DEMO")

    def test_gold_set_declares_what_it_is_not(self):
        honesty = self.gold["honesty"]
        self.assertIn("not production data", honesty["nature"].lower())
        self.assertIn("not statistically representative", honesty["size"].lower())

    def test_covers_the_failure_categories_it_claims_to(self):
        categories = {c["category"] for c in self.gold["queries"]}
        for expected in ("exact_terminology", "paraphrase", "multi_section", "irrelevant"):
            self.assertIn(expected, categories)

    def test_10_multiple_relevant_chunks_are_representable(self):
        multi = [c for c in self.gold["queries"] if len(c["relevant_sections"]) > 1]
        self.assertTrue(multi, "at least one query must span two sections")

    def test_4_irrelevant_query_has_an_empty_relevant_set(self):
        irrelevant = [c for c in self.gold["queries"] if c["category"] == "irrelevant"]
        self.assertTrue(irrelevant)
        for case in irrelevant:
            self.assertEqual(case["relevant_sections"], [])


class RetrievalMetricTests(SimpleTestCase):
    """Metric arithmetic against hand-computable cases, so a wrong formula cannot hide behind a
    plausible-looking score."""

    def _result(self, expected, retrieved):
        return retrieval_evaluation.QueryResult(
            query_id="T", query="q", sop_code="SOP-X", category="test",
            expected_sections=[], expected_chunk_ids=expected,
            retrieved_chunk_ids=retrieved, retrieved_sections=[],
            candidate_pool=len(retrieved),
            first_relevant_rank=next(
                (i for i, c in enumerate(retrieved, 1) if c in expected), None),
        )

    def test_5_recall_at_k(self):
        single = self._result(expected=[2], retrieved=[1, 2, 3])
        self.assertEqual(single.recall_at(1), 0.0)
        self.assertEqual(single.recall_at(2), 1.0)
        multi = self._result(expected=[2, 3], retrieved=[1, 2, 3])
        self.assertEqual(multi.recall_at(2), 0.5)
        self.assertEqual(multi.recall_at(3), 1.0)

    def test_6_precision_at_1(self):
        self.assertEqual(self._result([1], [1, 2, 3]).precision_at(1), 1.0)
        self.assertEqual(self._result([2], [1, 2, 3]).precision_at(1), 0.0)

    def test_7_hit_at_1(self):
        self.assertTrue(self._result([1], [1, 2]).hit_at_1)
        self.assertFalse(self._result([2], [1, 2]).hit_at_1)

    def test_8_mrr(self):
        self.assertEqual(self._result([1], [1, 2, 3]).reciprocal_rank, 1.0)
        self.assertEqual(self._result([2], [1, 2, 3]).reciprocal_rank, 0.5)
        self.assertEqual(self._result([3], [1, 2, 3]).reciprocal_rank, 1 / 3)
        self.assertEqual(self._result([9], [1, 2, 3]).reciprocal_rank, 0.0)

    def test_9_empty_retrieval(self):
        empty = self._result(expected=[1], retrieved=[])
        self.assertEqual(empty.recall_at(1), 0.0)
        self.assertEqual(empty.precision_at(1), 0.0)
        self.assertEqual(empty.reciprocal_rank, 0.0)

    def test_metrics_are_undefined_not_zero_when_nothing_is_relevant(self):
        """Recall over an empty relevant set is undefined. Averaging a 0.0 in would understate
        every other query, so the irrelevant case is excluded from scoring instead."""
        none_relevant = self._result(expected=[], retrieved=[1, 2])
        self.assertIsNone(none_relevant.recall_at(1))
        self.assertIsNone(none_relevant.precision_at(1))
        self.assertIsNone(none_relevant.reciprocal_rank)


class RetrievalBaselineTests(APITestCase):
    """The evaluator run over real chunks, through the production retrieval function."""

    def setUp(self):
        self.sop = SOPDocument.objects.create(
            title="Cleanroom Entry and Gowning", sop_code="SOP-300", version="v1.0",
            department="Production", file="f.txt", status="processed",
        )
        self.purpose = SOPChunk.objects.create(
            sop=self.sop, section_title="Section 1: Purpose", chunking_strategy="heading",
            chunk_text="This SOP defines the mandatory gowning sequence for personnel entering "
                       "a Grade B cleanroom in the production area.")
        self.sequence = SOPChunk.objects.create(
            sop=self.sop, section_title="Section 2: Gowning Sequence", chunking_strategy="heading",
            chunk_text="Personnel must don garments in the following strict order: hair cover, "
                       "face mask, sterile coverall, safety goggles, and sterile gloves last.")
        self.time_limit = SOPChunk.objects.create(
            sop=self.sop, section_title="Section 3: Time Limit", chunking_strategy="heading",
            chunk_text="A single gowning cycle inside the Grade B area is limited to a maximum "
                       "of four hours.")

    def _run(self, queries):
        import tempfile
        from pathlib import Path
        payload = {"version": "test", "corpus": {"sop_codes": ["SOP-300"], "excluded": {}},
                   "labelling": {}, "honesty": {}, "queries": queries}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
            json.dump(payload, fh)
            path = fh.name
        return retrieval_evaluation.evaluate_retrieval(Path(path))

    def test_3_known_relevant_chunk_is_retrieved_and_ranked(self):
        report = self._run([{
            "id": "T1", "query": "What is the maximum time inside the Grade B area?",
            "sop_code": "SOP-300", "relevant_sections": ["Section 3: Time Limit"],
            "category": "exact_terminology", "notes": "n",
        }])
        result = report.results[0]
        self.assertIn(self.time_limit.id, result.retrieved_chunk_ids)
        self.assertEqual(result.first_relevant_rank, 1)

    def test_11_provenance_survives_evaluation(self):
        """Every retrieved chunk must stay traceable to its document and section."""
        report = self._run([{
            "id": "T2", "query": "gowning order garments", "sop_code": "SOP-300",
            "relevant_sections": ["Section 2: Gowning Sequence"],
            "category": "exact_terminology", "notes": "n",
        }])
        result = report.results[0]
        self.assertEqual(result.sop_code, "SOP-300")
        self.assertEqual(len(result.retrieved_chunk_ids), len(result.retrieved_sections))
        for chunk_id, title in zip(result.retrieved_chunk_ids, result.retrieved_sections):
            chunk = SOPChunk.objects.get(id=chunk_id)
            self.assertEqual(chunk.section_title, title)
            self.assertEqual(chunk.sop.sop_code, "SOP-300")

    def test_gold_set_keys_on_titles_so_it_survives_reseeding(self):
        """Chunk primary keys change when data is reseeded; section titles do not."""
        report = self._run([{
            "id": "T3", "query": "gowning", "sop_code": "SOP-300",
            "relevant_sections": ["Section 2: Gowning Sequence"],
            "category": "exact_terminology", "notes": "n",
        }])
        self.assertEqual(report.results[0].expected_chunk_ids, [self.sequence.id])

    def test_unresolvable_label_is_skipped_not_scored_zero(self):
        report = self._run([{
            "id": "T4", "query": "x", "sop_code": "SOP-300",
            "relevant_sections": ["Section 9: Does Not Exist"],
            "category": "exact_terminology", "notes": "n",
        }])
        self.assertEqual(report.results, [])
        self.assertEqual(len(report.skipped), 1)
        self.assertIn("not found", report.skipped[0]["reason"])

    def test_summary_flags_that_retrieval_does_not_filter(self):
        report = self._run([{
            "id": "T5", "query": "gowning", "sop_code": "SOP-300",
            "relevant_sections": ["Section 2: Gowning Sequence"],
            "category": "exact_terminology", "notes": "n",
        }])
        summary = retrieval_evaluation.summarise(report)
        self.assertFalse(summary["corpus"]["retrieval_filters"])
        self.assertIn("DEGENERATE", summary["metric_caveats"]["recall_at_5"])

    def test_12_irrelevant_query_is_excluded_from_scoring(self):
        report = self._run([
            {"id": "T6", "query": "annual leave policy", "sop_code": "SOP-300",
             "relevant_sections": [], "category": "irrelevant", "notes": "n"},
            {"id": "T7", "query": "gowning order garments", "sop_code": "SOP-300",
             "relevant_sections": ["Section 2: Gowning Sequence"],
             "category": "exact_terminology", "notes": "n"},
        ])
        self.assertEqual(len(report.results), 2)
        self.assertEqual(len(report.scored), 1)
        self.assertEqual(retrieval_evaluation.summarise(report)["queries_scored"], 1)
