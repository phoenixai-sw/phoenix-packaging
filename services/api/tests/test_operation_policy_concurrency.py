"""A previously absent policy pointer must lock the seed decision through commit."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event, get_ident

import pytest
from sqlalchemy import delete, event, select

from services.api.billing.policy import pricing, aware
from services.api.billing.service import _action_cost
from services.api.models import Tenant, User, LoginSession
from services.api.feature_models import Membership
from services.api.operations.models import ActivePolicy, PolicyVersion
from services.api.errors import APIError
from services.api.operations.schemas import CreatePolicy, PublishPolicy
from services.api.operations.service import create_policy, image_defaults, image_settings, publish_policy
from services.api.tests.test_api import app, client, register


@pytest.mark.parametrize('kind', ['pricing', 'image'])
def test_absent_pointer_keeps_seed_snapshot_until_reader_commit(app, client, kind):
    sessions = app.state.session_factory
    settings = app.state.settings
    with sessions() as db:
        tenant = Tenant(name='Isolated concurrency test')
        db.add(tenant); db.flush()
        user = User(tenant_id=tenant.id, name='Policy QA', email='policy-concurrency@example.test')
        db.add(user); db.flush()
        payload = pricing() if kind == 'pricing' else image_defaults(settings)
        payload.pop('version', None)
        if kind == 'pricing':
            payload['actions']['image.generate.standard'] = 12
        else:
            payload['default_model'] = 'gpt-image-2.5-flare'
        draft = create_policy(db, user, CreatePolicy(kind=kind, payload=payload,
            reason='Isolated first-publication synchronization test'), settings)
        user_id, draft_id, draft_version = user.id, draft.id, draft.version
        # Simulate a create_all / legacy local DB with no baseline pointer.
        db.execute(delete(ActivePolicy).where(ActivePolicy.kind == kind))
        db.commit()
        engine = db.get_bind()

    entered_write, committed = Event(), Event()
    writer_ident = [None]

    def observe_write(connection, cursor, statement, parameters, context, executemany):
        if get_ident() == writer_ident[0] and statement.lstrip().upper().startswith('INSERT INTO OPERATION_ACTIVE_POLICIES'):
            entered_write.set()

    def writer():
        writer_ident[0] = get_ident()
        with sessions() as db:
            published = publish_policy(db, db.get(User, user_id), draft_id,
                PublishPolicy(expected_active_id=None, reason='Concurrent policy publication',
                    understands_existing_subscriptions_unchanged=True), settings)
            db.commit()
            committed.set()
            return published.id

    def read(db):
        if kind == 'pricing':
            value = pricing(db)
            return value['version'], _action_cost('image.generate.standard', 1, db)
        return image_settings(db, settings).image_model

    event.listen(engine, 'before_cursor_execute', observe_write)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with sessions() as db:
                first = read(db)
                future = pool.submit(writer)
                try:
                    assert entered_write.wait(5), 'The publisher never reached its pointer write.'
                    # The read transaction created/locked the absent pointer;
                    # publication cannot slip between cost and version reads.
                    assert not committed.wait(.3), 'Policy changed inside the seed read transaction.'
                    assert read(db) == first
                finally:
                    db.commit()  # Release the test reader even after an assertion.
            assert future.result(timeout=5) == draft_id
        with sessions() as db:
            assert read(db) == ((draft_version, 12) if kind == 'pricing' else 'gpt-image-2.5-flare')
    finally:
        event.remove(engine, 'before_cursor_execute', observe_write)


@pytest.mark.parametrize('platform_admin', [True, False])
def test_global_policy_authority_ignores_selected_team_viewer_but_keeps_admin_and_csrf(app, client, platform_admin):
    auth = register(client)
    if platform_admin:
        app.state.settings.admin_emails = (auth['user']['email'],)
    with app.state.session_factory() as db:
        team = Tenant(name='Unpaid selected viewer team')
        db.add(team); db.flush()
        db.add(Membership(tenant_id=team.id, user_id=auth['user']['id'], role='viewer', is_active=True))
        session = db.scalar(select(LoginSession).where(LoginSession.user_id == auth['user']['id']))
        session.active_tenant_id = team.id
        db.commit()
    response = client.get('/v1/admin/policies')
    if not platform_admin:
        assert response.status_code == 403 and response.json()['code'] == 'ADMIN_REQUIRED'
        # Supplying a valid policy does not grant an ordinary viewer authority.
        policy = pricing(); policy.pop('version')
        denied = client.post('/v1/admin/policies', json={'kind': 'pricing',
            'payload': policy, 'reason': 'Ordinary viewer authorization test'})
        assert denied.status_code == 403 and denied.json()['code'] == 'ADMIN_REQUIRED'
        return
    assert response.status_code == 200, response.text
    payload = response.json()['data']['pricing']
    csrf = client.headers.pop('X-CSRF-Token')
    body = {'kind': 'pricing', 'payload': payload, 'reason': 'Global operator policy authority test'}
    rejected = client.post('/v1/admin/policies', json=body)
    assert rejected.status_code == 403 and rejected.json()['code'] == 'CSRF_REJECTED'
    client.headers['X-CSRF-Token'] = csrf
    draft = client.post('/v1/admin/policies', json=body)
    assert draft.status_code == 201, draft.text
    published = client.post('/v1/admin/policies/' + draft.json()['data']['id'] + '/publish', json={
        'expected_active_id': None, 'reason': 'Publish without tenant entitlement lock',
        'understands_existing_subscriptions_unchanged': True})
    assert published.status_code == 200, published.text


def test_stale_unpublished_row_cannot_republish_after_other_transaction_commits(app, client):
    sessions = app.state.session_factory
    with sessions() as db:
        tenant = Tenant(name='Stale publication fixture')
        db.add(tenant); db.flush()
        user = User(tenant_id=tenant.id, name='Policy QA', email='stale-publication@example.test')
        db.add(user); db.flush()
        payload = pricing(); payload.pop('version')
        draft = create_policy(db, user, CreatePolicy(kind='pricing', payload=payload,
            reason='Stale policy publication regression'), app.state.settings)
        user_id, identity = user.id, draft.id
        db.commit()
    with sessions() as stale_reader:
        # Strong reference intentionally preserves the old identity-map object.
        stale = stale_reader.get(PolicyVersion, identity)
        assert stale.published_at is None
        with sessions() as publisher:
            row = publish_policy(publisher, publisher.get(User, user_id), identity,
                PublishPolicy(expected_active_id=None, reason='First committed publication',
                    understands_existing_subscriptions_unchanged=True), app.state.settings)
            publisher.commit()
            first_published_at = aware(row.published_at)
        assert stale.published_at is None
        with pytest.raises(APIError) as error:
            publish_policy(stale_reader, stale_reader.get(User, user_id), identity,
                PublishPolicy(expected_active_id=identity, reason='Must not overwrite publication time',
                    understands_existing_subscriptions_unchanged=True), app.state.settings)
        assert error.value.code == 'POLICY_ALREADY_PUBLISHED'
        stale_reader.rollback()
    with sessions() as db:
        assert aware(db.get(PolicyVersion, identity).published_at) == first_published_at
        pointer = db.get(ActivePolicy, 'pricing')
        assert pointer.version_id == identity and pointer.revision == 1
