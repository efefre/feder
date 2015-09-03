from __future__ import division
from itertools import groupby
from django.conf import settings
from django.core.urlresolvers import reverse
from django.db import models
from django.db.models.signals import post_delete, post_save
from django.utils.translation import ugettext_lazy as _
from jsonfield import JSONField
from model_utils.managers import PassThroughManager
from model_utils.models import TimeStampedModel

from feder.alerts.models import Alert
from feder.cases.models import Case
from feder.questionaries.models import Question, Questionary
from feder.questionaries.modulator import modulators

from .utils import all_answer_equal

_('Tasks index')


class TaskQuerySet(models.QuerySet):

    def survey_count(self):
        return self.annotate(survey_count=models.Count('survey'))

    def survey_left(self):
        return self.annotate(survey_left=models.F('survey_done') - models.F('survey_required'))

    def is_done(self, exclude=False):
        func = self.exclude if exclude else self.filter
        return func(survey_required__lte=models.F('survey_done'))

    def update_survey_done(self):
        for obj in self.survey_count().all():
            obj.survey_done = obj.survey_count
            obj.save()

    def by_monitoring(self, monitoring):
        return self.filter(case__monitoring=monitoring)

    def exclude_by_user(self, user, monitoring=None):
        filled_set = Survey.objects.filter(user=user).all().values('task_id')
        return self.exclude(pk__in=filled_set)

    def survey_stats(self):
        result = self.aggregate(done_count=models.Sum('survey_done'),
                                required_count=models.Sum('survey_required'))
        if result['required_count']:
            result['progress'] = result.get('done_count', 0) / result.get('required_count', 0) * 100
        else:
            result['progress'] = 0
        return result

    def area(self, jst):
        return self.filter(case__institution__jst__tree_id=jst.tree_id,
                           case__institution__jst__lft__range=(jst.lft, jst.rght))


class Task(TimeStampedModel):
    name = models.CharField(max_length=75, verbose_name=_("Name"))
    case = models.ForeignKey(Case, verbose_name=_("Case"))
    questionary = models.ForeignKey(Questionary, verbose_name=_("Questionary"),
                                    help_text=_("Questionary to fill by user as task"))
    survey_required = models.SmallIntegerField(verbose_name=_("Required survey count"), default=2)
    survey_done = models.SmallIntegerField(verbose_name=_("Done survey count"), default=0)
    objects = PassThroughManager.for_queryset_class(TaskQuerySet)()

    def __unicode__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('tasks:details', kwargs={'pk': self.pk})

    def lock_check(self):
        if self.questionary.lock is False:
            self.questionary.lock = True
            self.questionary.save()
            return True
        return False

    def progress(self):
        if self.survey_required <= 0:
            return 1
        return (self.survey_done / self.survey_required * 100)

    def survey_left(self):
        return self.survey_required - self.survey_done

    def is_done(self):
        return (self.survey_required <= self.survey_done)

    def save(self, lock_check=True, *args, **kwargs):
        if lock_check:
            self.lock_check()
        return super(Task, self).save(*args, **kwargs)

    def get_next_for_user(self, user):
        return (Task.objects.by_monitoring(self.case.monitoring).
                exclude_by_user(user).first())

    def verify(self):
        object_list = list(Answer.objects.filter(survey__task=self).
                           select_related('survey').
                           order_by('question_id').all())
        if not object_list:  # empty always pass
            return True
        if object_list[0].survey.credibility > 2:  # selected survey always pass
            return True
        for question, answer_by_question in groupby(object_list, lambda x: x.question_id):
            if all_answer_equal(answer_by_question):
                return True
            else:
                return False

    class Meta:
        ordering = ['created', ]
        verbose_name = _("Task")
        verbose_name_plural = _("Tasks")


class SurveyQuerySet(models.QuerySet):

    def with_full_answer(self):
        qs = Answer.objects.select_related('question')
        return self.prefetch_related(models.Prefetch('answer_set', queryset=qs))

    def with_user(self):
        return self.select_related('user')

    def for_task(self, task):
        return self.filter(task=task)


class Survey(TimeStampedModel):
    task = models.ForeignKey(Task)
    user = models.ForeignKey(settings.AUTH_USER_MODEL)
    objects = PassThroughManager.for_queryset_class(SurveyQuerySet)()
    credibility = models.PositiveIntegerField(default=0, verbose_name=_("Credibility"))

    class Meta:
        ordering = ['credibility', 'created']
        verbose_name = _("Survey")
        verbose_name_plural = _("Surveys")
        unique_together = [('task', 'user')]


class Answer(models.Model):
    survey = models.ForeignKey(Survey)
    question = models.ForeignKey(Question)
    blob = JSONField()

    def render(self):
        return modulators[self.question.genre](self.question.blob).render_answer(self.blob)

    class Meta:
        verbose_name = _("Answer")
        verbose_name_plural = _("Answers")


def increase_task_survey_done(sender, instance, created, **kwargs):
    if created:
        Task.objects.filter(pk=instance.task_id).update(survey_done=models.F('survey_done') + 1)

post_save.connect(increase_task_survey_done, sender=Survey, dispatch_uid="increase_task_done")


def raise_alert_if_mismatch(sender, instance, created, **kwargs):
    if not instance.task.verify():
        reason_text = "Answer mismatch - verification fail"
        Alert.objects.create(monitoring=instance.task.case.monitoring,
                             reason=reason_text,
                             author=instance.user,
                             link_object=instance.task)

post_save.connect(raise_alert_if_mismatch, sender=Survey, dispatch_uid="raise_alert_if_mismatch")


def decrease_task_survey_done(sender, instance, **kwargs):
    Task.objects.filter(pk=instance.task_id).update(survey_done=models.F('survey_done') - 1)

post_delete.connect(decrease_task_survey_done, sender=Survey, dispatch_uid="decrease_task_done")
