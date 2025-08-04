# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.core.exceptions import ValidationError
from django.urls import reverse
from django.db import models
from django.utils import timezone
from django.utils.encoding import force_str
from django.utils.translation import override, gettext_lazy as _, gettext

from cms.models import CMSPlugin
from cms.models.fields import PlaceholderField
from cms.utils.i18n import get_current_language, get_redirect_on_fallback

from aldryn_translation_tools.models import (
    TranslationHelperMixin, TranslatedAutoSlugifyMixin,
)
from djangocms_text_ckeditor.fields import HTMLField
from extended_choices import Choices
from filer.fields.image import FilerImageField
from parler.models import TranslatableModel, TranslatedFields
from sortedm2m.fields import SortedManyToManyField

from .cms_appconfig import EventsConfig
from .conf import settings
from .managers import EventManager
from .utils import get_additional_styles, date_or_datetime

STANDARD = 'standard'


class Event(TranslatedAutoSlugifyMixin,
            TranslationHelperMixin,
            TranslatableModel):

    slug_source_field_name = 'title'

    start_date = models.DateField(_('start date'))
    start_time = models.TimeField(_('start time'), null=True, blank=True)
    end_date = models.DateField(_('end date'), null=True, blank=True)
    end_time = models.TimeField(_('end time'), null=True, blank=True)
    # TODO: add timezone (optional and purely for display purposes)

    is_published = models.BooleanField(
        _('is published'), default=True,
        help_text=_('wether the event should be displayed')
    )
    publish_at = models.DateTimeField(
        _('publish at'), default=timezone.now,
        help_text=_('time at which the event should be published')
    )
    detail_link = models.URLField(
        _('external link'), blank=True, default='',
        help_text=_('external link to details about this event')
    )
    register_link = models.URLField(
        _('registration link'), blank=True, default='',
        help_text=_('link to an external registration system')
    )
    enable_registration = models.BooleanField(
        _('enable event registration'), default=False
    )
    registration_deadline_at = models.DateTimeField(
        _('allow registration until'), null=True, blank=True, default=None
    )
    event_coordinators = models.ManyToManyField(
        'EventCoordinator', verbose_name=_('event coordinators'), blank=True
    )
    description = PlaceholderField(
        'aldryn_events_event_description', verbose_name=_('description'),
    )

    translations = TranslatedFields(
        title=models.CharField(
            _('title'), max_length=150, help_text=_('translated')
        ),
        slug=models.SlugField(
            _('slug'), null=False, blank=True, max_length=150
        ),
        short_description=HTMLField(
            _('short description'), blank=True, default='',
            help_text=_('translated')
        ),
        location=models.TextField(_('location'), blank=True, default=''),
        location_lat=models.FloatField(
            _('location latitude'), blank=True, null=True
        ),
        location_lng=models.FloatField(
            _('location longitude'), blank=True, null=True
        ),
        image=FilerImageField(
            verbose_name=_('image'), null=True, blank=True,
            related_name='event_images', on_delete=models.SET_NULL
        ),
        meta={'unique_together': (('language_code', 'slug'),)}
    )
    app_config = models.ForeignKey(EventsConfig, verbose_name=_('app_config'), on_delete=models.CASCADE)

    objects = EventManager()

    class Meta:
        verbose_name = _('Event')
        verbose_name_plural = _('Events')
        # NOTE: Ordering is returning older events first. Please DO NOT CHANGE
        # this without also considering:
        #     `cms_appconfig.EventsConfig.latest_first`
        # and the QuerySet method:
        #     `managers.EventsQuerySet.namespace()`
        # which reverses this ordering when the option is set.
        ordering = ('start_date', 'start_time', 'end_date', 'end_time')

    def get_title(self):
        return self.safe_translation_getter('title', any_language=True)

    def __str__(self):
        # since we now have app configs, it is pretty handy to display them
        return '{0} ({1})'.format(
            self.get_title(),
            getattr(self.app_config, 'app_title', self.app_config.namespace))

    @property
    def start_at(self):
        return self.start()

    @property
    def end_at(self):
        return self.end()

    def clean(self):
        # there is a start date and end date
        if self.start_date and self.end_date:
            if self.end_date < self.start_date:
                raise ValidationError(
                    _('Start date should be before end date.'))

            # dates are equal, check time
            if self.start_date == self.end_date:
                # check that time is provided
                if not (self.end_time and self.start_time):
                    raise ValidationError(
                        _('When specifying identical start and end dates, '
                          'please also provide the start and end time.'))

                # check time validity
                if (self.end_time < self.start_time or
                        self.start_time == self.end_time):
                    raise ValidationError(
                        _('For same start and end dates start time '
                          'should be before end time.'))

        if self.enable_registration and self.register_link:
            raise ValidationError(
                _("the registration system can't be active if there is "
                  "an external registration link. please remove at least one "
                  "of the two.")
            )

        if self.enable_registration and not self.registration_deadline_at:
            raise ValidationError(_("please select a registration deadline."))

    def start(self):
        return date_or_datetime(self.start_date, self.start_time)

    def end(self):
        return date_or_datetime(self.end_date, self.end_time)

    @property
    def days(self):
        """
        Return the number of days between start_date and end_date.
        If end_date is null, consider start_date as end_date.
        Minimal value will be always 1 because a event has at
        least 1 day.

        :return: number of days
        """
        # Need to normalize values to date objects cuz it can be strings
        # and Django does not normalize in some situations, like when
        # using 'Event.objects.create'
        start_date_field = self._meta.get_field('start_date')
        end_date_field = self._meta.get_field('end_date')
        self.start_date = start_date_field.to_python(self.start_date)
        if self.end_date:
            self.end_date = end_date_field.to_python(self.end_date)

        end_date = self.end_date or self.start_date
        return (end_date - self.start_date).days + 1

    @property
    def takes_single_day(self):
        """ True if event take a single day, else False """
        return self.days == 1

    @property
    def is_registration_deadline_passed(self):
        return not (self.registration_deadline_at and
                    self.registration_deadline_at > timezone.now())

    def get_url_name(self):
        try:
            url_name = '{0}:events_detail'.format(self.app_config.namespace)
        except AttributeError:
            url_name = 'aldryn_events:events_detail'

        return url_name

    def get_absolute_url(self, language=None):

        if not language:
            language = get_current_language()

        kwargs = {}
        slug, slug_lang = self.known_translation_getter(
            'slug', default=None, language_code=language)

        kwargs.update(slug=slug)
        if slug and slug_lang:
            site_id = getattr(settings, 'SITE_ID', None)
            if get_redirect_on_fallback(language, site_id):
                language = slug_lang

        if self.app_config_id and self.app_config.namespace:
            namespace = '{0}:'.format(self.app_config.namespace)
        else:
            namespace = ''

        with override(language):
            return reverse('{0}events_detail'.format(namespace), kwargs=kwargs)


class EventCoordinator(models.Model):

    name = models.CharField(max_length=200, blank=True)
    email = models.EmailField(max_length=80, blank=True)
    user = models.OneToOneField(
        to=getattr(settings, 'AUTH_USER_MODEL', 'auth.User'),
        verbose_name=_('user'),
        null=True,
        blank=True,
        on_delete=models.CASCADE
    )

    def __str__(self):
        return self.full_name or self.email_address

    def clean(self):
        if not self.email:
            if not self.user_id or not self.user.email:
                raise ValidationError(
                    _('Please define an email for the coordinator.')
                )

    def get_email_address(self):
        email = self.email

        if not email and self.user_id:
            email = self.user.email
        return email
    get_email_address.short_description = _('email')

    def get_name(self):
        name = self.name
        if not name and self.user_id:
            name = self.user.get_full_name()
        return name
    get_name.short_description = _('name')

    email_address = property(get_email_address)
    full_name = property(get_name)


class Registration(models.Model):
    SALUTATIONS = Choices(
        ('SALUTATION_FEMALE', 'mrs', gettext('Ms.')),
        ('SALUTATION_MALE', 'mr', gettext('Mr.')),
    )

    created_at = models.DateTimeField(auto_now_add=True)
    modified_at = models.DateTimeField(auto_now=True)

    language_code = models.CharField(
        choices=settings.LANGUAGES, default=settings.LANGUAGES[0][0],
        max_length=32
    )

    event = models.ForeignKey(Event, on_delete=models.CASCADE)
    salutation = models.CharField(
        _('Salutation'), max_length=5, choices=SALUTATIONS,
        default=SALUTATIONS.SALUTATION_FEMALE
    )
    company = models.CharField(
        _('Company'), max_length=100, blank=True, default=''
    )
    first_name = models.CharField(_('First name'), max_length=100)
    last_name = models.CharField(_('Last name'), max_length=100)

    address = models.TextField(_('Address'), blank=True, default='')
    address_zip = models.CharField(_('ZIP CODE'), max_length=20)
    address_city = models.CharField(_('City'), max_length=100)

    phone = models.CharField(
        _('Phone number'), blank=True, default='', max_length=20
    )
    mobile = models.CharField(
        _('Mobile number'), blank=True, default='', max_length=20
    )
    email = models.EmailField(_('E-Mail'))

    message = models.TextField(_('Message'), blank=True, default='')

    @property
    def address_street(self):
        return self.address


class BaseEventPlugin(CMSPlugin):
    app_config = models.ForeignKey(EventsConfig, verbose_name=_('app_config'), on_delete=models.CASCADE)

    # Add an app namespace to related_name to avoid field name clashes
    # with any other plugins that have a field with the same name as the
    # lowercase of the class name of this model.
    # https://github.com/divio/django-cms/issues/5030
    cmsplugin_ptr = models.OneToOneField(
        CMSPlugin,
        related_name='%(app_label)s_%(class)s',
        parent_link=True,
        on_delete=models.CASCADE,
    )

    def copy_relations(self, old_instance):
        self.app_config = old_instance.app_config

    class Meta:
        abstract = True


class EventListPlugin(BaseEventPlugin):
    STYLE_CHOICES = [
        (STANDARD, _('Standard')),
    ]

    style = models.CharField(
        verbose_name=_('Style'),
        choices=STYLE_CHOICES + get_additional_styles(),
        default=STANDARD,
        max_length=50
    )
    events = SortedManyToManyField(Event, blank=True)

    def __str__(self):
        return force_str(self.pk)

    def copy_relations(self, oldinstance):
        super(EventListPlugin, self).copy_relations(oldinstance)
        # With Django 1.5 and because a bug in SortedManyToManyField
        # we can not use oldinstance.events or we get a error like:
        # DatabaseError:
        #   no such column: aldryn_events_eventlistplugin_events.sort_value
        self.events = Event.objects.filter(eventlistplugin__pk=oldinstance.pk)


class UpcomingPluginItem(BaseEventPlugin):
    STYLE_CHOICES = [
        (STANDARD, _('Standard')),
    ]

    FUTURE_EVENTS = _('future events')
    PAST_EVENTS = _('past events')
    BOOL_CHOICES = (
        (False, FUTURE_EVENTS),
        (True, PAST_EVENTS),
    )

    past_events = models.BooleanField(
        verbose_name=_('selection'),
        choices=BOOL_CHOICES,
        default=False,
    )
    style = models.CharField(
        verbose_name=_('Style'),
        choices=STYLE_CHOICES + get_additional_styles(),
        default=STANDARD,
        max_length=50
    )
    latest_entries = models.PositiveSmallIntegerField(
        verbose_name=_('latest entries'),
        default=5,
        help_text=_('The number of latest events to be displayed.')
    )

    cache_duration = models.PositiveSmallIntegerField(
        default=0,  # not the most sensible, but consistent with older versions
        blank=False,
        help_text=_(
            "The maximum duration (in seconds) that this plugin's content "
            "should be cached.")
    )

    def __str__(self):
        return force_str(
            self.PAST_EVENTS if self.past_events else self.FUTURE_EVENTS
        )


class EventCalendarPlugin(BaseEventPlugin):

    cache_duration = models.PositiveSmallIntegerField(
        default=0,  # not the most sensible, but consistent with older versions
        blank=False,
        help_text=_(
            "The maximum duration (in seconds) that this plugin's content "
            "should be cached.")
    )

    def __str__(self):
        return force_str(self.pk)
