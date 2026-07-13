from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import JobRole, LearnerProfile
from accounts.permissions import SME_GROUP
from attempts.models import AttemptAnswer, QuizAttempt
from quiz.models import Option, Question
from sops.models import SOPChunk, SOPDocument


class Command(BaseCommand):
    help = "Create realistic demo data for the GxP Training Bot project."

    def handle(self, *args, **options):
        AttemptAnswer.objects.all().delete()
        QuizAttempt.objects.all().delete()
        Option.objects.all().delete()
        Question.objects.all().delete()
        SOPChunk.objects.all().delete()
        SOPDocument.objects.all().delete()
        LearnerProfile.objects.all().delete()
        JobRole.objects.all().delete()

        User = get_user_model()
        User.objects.filter(username__in=["anjali", "vikram", "rohit", "priya", "arun", "sneha", "karan"]).delete()

        roles = {
            "Production Operator": JobRole.objects.create(
                name="Production Operator",
                department="Production",
                description="Handles production floor activities and cleanroom procedures.",
            ),
            "QA Analyst": JobRole.objects.create(
                name="QA Analyst",
                department="Quality Assurance",
                description="Reviews SOP compliance, deviations, and training records.",
            ),
            "QC Chemist": JobRole.objects.create(
                name="QC Chemist",
                department="Quality Control",
                description="Performs laboratory testing and instrument suitability checks.",
            ),
            "Warehouse Staff": JobRole.objects.create(
                name="Warehouse Staff",
                department="Warehouse",
                description="Manages material receipt, quarantine, and storage controls.",
            ),
            "Maintenance Technician": JobRole.objects.create(
                name="Maintenance Technician",
                department="Engineering",
                description="Maintains equipment and preventive maintenance logs.",
            ),
        }

        learners = [
            ("rohit", "Rohit", "Mehta", "Production Operator", "EMP-101"),
            ("priya", "Priya", "Nair", "QA Analyst", "EMP-102"),
            ("arun", "Arun", "Verma", "Warehouse Staff", "EMP-103"),
            ("sneha", "Sneha", "Kapoor", "QC Chemist", "EMP-104"),
            ("karan", "Karan", "Singh", "Maintenance Technician", "EMP-105"),
        ]
        users = {}
        for username, first_name, last_name, role_name, employee_code in learners:
            user = User.objects.create_user(
                username=username,
                email=f"{username}@pharma.co",
                password="demo12345",
                first_name=first_name,
                last_name=last_name,
            )
            LearnerProfile.objects.create(user=user, job_role=roles[role_name], employee_code=employee_code)
            users[username] = user

        qa_lead = User.objects.create_user(
            username="anjali",
            email="anjali.rao@pharma.co",
            password="demo12345",
            first_name="Anjali",
            last_name="Rao",
            is_staff=True,
        )

        sme_group, _ = Group.objects.get_or_create(name=SME_GROUP)
        sme_reviewer = User.objects.create_user(
            username="vikram",
            email="vikram.desai@pharma.co",
            password="demo12345",
            first_name="Vikram",
            last_name="Desai",
        )
        sme_reviewer.groups.add(sme_group)

        sop_rows = [
            ("SOP-217", "Aseptic Gowning Procedure", "Production", "v2.1", "processed"),
            ("SOP-214", "Equipment Cleaning and Sanitization", "Production", "v3.0", "processed"),
            ("SOP-211", "Deviation Reporting", "Quality Assurance", "v1.4", "processed"),
            ("SOP-208", "Warehouse Material Receipt", "Warehouse", "v2.0", "uploaded"),
            ("SOP-204", "HPLC Calibration", "Quality Control", "v1.2", "processed"),
            ("SOP-198", "Preventive Maintenance Log", "Engineering", "v1.0", "failed"),
        ]
        sops = {}
        for code, title, department, version, status in sop_rows:
            sop = SOPDocument.objects.create(
                title=title,
                sop_code=code,
                version=version,
                department=department,
                file=f"sops/{code.lower()}.pdf",
                status=status,
                uploaded_by=qa_lead,
            )
            if status == "processed":
                SOPChunk.objects.create(
                    sop=sop,
                    section_title="Training Scope",
                    page_number=1,
                    chunk_text=f"{title} defines role-specific compliance steps for {department}.",
                )
            sops[code] = sop

        self.create_question(
            sop=sops["SOP-217"],
            role=roles["Production Operator"],
            chunk=sops["SOP-217"].chunks.first(),
            question_text="During aseptic gowning, at what point should sterile gloves be donned?",
            difficulty="medium",
            explanation="Sterile gloves are donned near the end of the sequence to reduce contamination risk.",
            options=[
                ("Before entering the airlock", False),
                ("After donning the sterile coverall and goggles", True),
                ("After sanitizing hands but before the coverall", False),
                ("Only after entering the Grade B area", False),
            ],
            status="draft",
        )
        self.create_question(
            sop=sops["SOP-204"],
            role=roles["QC Chemist"],
            chunk=sops["SOP-204"].chunks.first(),
            question_text="What is the acceptable HPLC system suitability tailing factor as per SOP-204?",
            difficulty="hard",
            explanation="The SOP acceptance criterion allows a tailing factor of less than or equal to 2.0.",
            options=[
                ("Less than or equal to 1.0", False),
                ("Less than or equal to 1.5", False),
                ("Less than or equal to 2.0", True),
                ("Less than or equal to 3.0", False),
            ],
            status="draft",
        )
        self.create_question(
            sop=sops["SOP-211"],
            role=roles["QA Analyst"],
            chunk=sops["SOP-211"].chunks.first(),
            question_text="Why should deviations be documented immediately?",
            difficulty="medium",
            explanation="Immediate documentation preserves data integrity and supports timely impact assessment.",
            options=[
                ("To delay investigation until batch release", False),
                ("To preserve accurate compliance evidence", True),
                ("To avoid notifying Quality Assurance", False),
                ("To reduce training records", False),
            ],
            status="approved",
        )

        attempts = [
            ("rohit", "Production Operator", "SOP-217", Decimal("88.00"), True),
            ("priya", "QA Analyst", "SOP-211", Decimal("94.00"), True),
            ("arun", "Warehouse Staff", "SOP-208", Decimal("62.00"), True),
            ("sneha", "QC Chemist", "SOP-204", Decimal("85.00"), True),
            ("karan", "Maintenance Technician", "SOP-198", Decimal("0.00"), False),
        ]
        for username, role_name, sop_code, score, completed in attempts:
            QuizAttempt.objects.create(
                learner=users[username],
                job_role=roles[role_name],
                sop=sops[sop_code],
                score=score,
                completed_at=timezone.now() if completed else None,
            )

        self.stdout.write(self.style.SUCCESS("Demo data created."))

    def create_question(self, sop, role, chunk, question_text, difficulty, explanation, options, status):
        question = Question.objects.create(
            sop=sop,
            job_role=role,
            source_chunk=chunk,
            question_text=question_text,
            difficulty=difficulty,
            explanation=explanation,
            status=status,
        )
        for option_text, is_correct in options:
            Option.objects.create(question=question, option_text=option_text, is_correct=is_correct)
        return question
