from django.urls import path

from .views import generate_quiz

urlpatterns = [
    path("generate/", generate_quiz, name="generate-quiz"),
]
