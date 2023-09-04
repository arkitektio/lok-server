from lord.enums import GrantType
from balder.types.mutation.base import BalderMutation
from balder.types.query.base import BalderQuery
from balder.types import BalderObject
from lord import types
from lord import models
from django.conf import settings
import graphene
from uuid import uuid4
import requests
import logging

logger = logging.getLogger(__name__)


class DeleteChannelResult(graphene.ObjectType):
    token = graphene.String()


class DeleteChannel(BalderMutation):
    class Arguments:
        token = graphene.String(description="The ID of the application", required=True)

    def mutate(root, info, *args, token=None):
        app = models.Channel.objects.get(token=token)
        app.delete()
        return {"token": token}

    class Meta:
        type = DeleteChannelResult


class CreateChannel(BalderMutation):
    class Arguments:
        name = graphene.String(
            description="The channel you want to create or update", required=False
        )
        token = graphene.String(description="The expo token", required=True)

    def mutate(root, info, *args, name=None, token: str = None):
        app, _ = models.Channel.objects.update_or_create(
            name=name, user=info.context.user, defaults=dict(token=token)
        )

        return app

    class Meta:
        type = types.Channel


class PublishResult(graphene.ObjectType):
    channel = graphene.Field(types.Channel)
    status = graphene.String()


class PublishToChannel(BalderMutation):
    class Arguments:
        channel = graphene.ID(required=True)
        message = graphene.String(required=True)
        title = graphene.String(required=True)

    def mutate(root, info, *args, channel=None, message=None, title=None):
        app = models.Channel.objects.get(id=channel)

        return {
            "channel": channel,
            "status": channel.publish(message=message, title=title),
        }

    class Meta:
        type = PublishResult


class NotifyUser(BalderMutation):
    class Arguments:
        user = graphene.ID(required=True)
        channels = graphene.List(graphene.String, required=False)
        message = graphene.String(required=True)
        title = graphene.String(required=True)

    def mutate(root, info, *args, user=None, channels=None, message=None, title=None):
        user = models.HerreUser.objects.get(id=user)
        if channels:
            publish_channels = models.Channel.objects.filter(
                user=user, name__in=channels
            ).all()
        else:
            publish_channels = models.Channel.objects.filter(user=user).all()

        results = []
        for channel in publish_channels:
            results.append(
                {
                    "channel": channel,
                    "status": channel.publish(message=message, title=title),
                }
            )

        return results

    class Meta:
        type = graphene.List(PublishResult)
