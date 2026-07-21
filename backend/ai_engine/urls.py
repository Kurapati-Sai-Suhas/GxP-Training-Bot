from django.urls import path

from .views import generate_quiz, sop_chat

urlpatterns = [
    path("generate/", generate_quiz, name="generate-quiz"),
    path("sop-chat/", sop_chat, name="sop-chat"),
]
