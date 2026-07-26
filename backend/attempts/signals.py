# TopicMastery is updated directly from QuizAttemptViewSet.submit() (see views.py),
# once per completed attempt using its overall score. It used to be updated here via a
# post_save signal on each individual AttemptAnswer, but that let the last question
# graded in a multi-question quiz silently overwrite the outcome of an otherwise-strong
# attempt. No signal receivers are registered in this module any more.
