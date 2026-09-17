"""Global operator authority does not grant access to the selected team's data."""
from pathlib import Path

import pytest
from sqlalchemy import select

from services.api.feature_models import Membership
from services.api.models import LoginSession, Tenant
from services.api.tests.test_api import app, client, project, register
from services.api.tests.test_service_orders import create
from tests.geometry_pdf.test_structure_v2 import separated


@pytest.mark.parametrize('route_kind', ['structure', 'icc', 'profile', 'service'])
@pytest.mark.parametrize('active_membership', [True, False], ids=['unpaid-viewer', 'removed-viewer'])
def test_global_admin_tools_ignore_team_scope_without_bypassing_authority_or_csrf(app, client, route_kind, active_membership):
    auth = register(client)
    app.state.settings.admin_emails = (auth['user']['email'],)
    home_project = project(client)
    icc_bytes = (Path(__file__).resolve().parents[3] / 'fixtures/icc/synthetic-cmyk-test.icc').read_bytes()
    if route_kind == 'profile':
        uploaded = client.post('/v1/admin/print-engine/icc',
            files={'file': ('fixture.icc', icc_bytes, 'application/vnd.iccprofile')},
            data={'source': 'Isolated test fixture', 'license': 'CC0-1.0'})
        assert uploaded.status_code == 201, uploaded.text
        icc = uploaded.json()['data']
    if route_kind == 'service':
        order = create(client)
    with app.state.session_factory() as db:
        team = Tenant(name='Unpaid selected team fixture')
        db.add(team); db.flush()
        db.add(Membership(tenant_id=team.id, user_id=auth['user']['id'], role='viewer', is_active=active_membership))
        session = db.scalar(select(LoginSession).where(LoginSession.user_id == auth['user']['id']))
        session.active_tenant_id = team.id
        db.commit()

    def invoke(**headers):
        if route_kind == 'structure':
            return client.post('/v1/admin/structures/validate', headers=headers,
                json={'structure_definition': separated(), 'inputs': {'width_mm': 160, 'height_mm': 230}})
        if route_kind == 'icc':
            return client.post('/v1/admin/print-engine/icc', headers=headers,
                files={'file': ('fixture.icc', icc_bytes, 'application/vnd.iccprofile')},
                data={'source': 'Isolated test fixture', 'license': 'CC0-1.0'})
        if route_kind == 'profile':
            return client.post('/v1/admin/print-engine/profiles', headers=headers,
                json={'name': 'Isolated profile', 'manufacturer': 'Fixture only', 'material': 'Fixture',
                      'source': 'Internal test', 'license': 'CC0-1.0', 'review_available': True,
                      'requirements': {'icc_id': icc['id'], 'icc_sha256': icc['sha256']}})
        return client.post(f"/v1/admin/service-orders/{order['id']}/quotes", headers=headers,
            json={'base_revision': order['revision'], 'amount_inc_vat': 100000,
                  'scope': 'An isolated support scope', 'exclusions': 'No actual service delivery',
                  'reason': 'Global operator scope fixture'})

    # A valid tenant/session and CSRF token are insufficient without the
    # separately derived, verified Google administrator identity.
    app.state.settings.admin_emails = ()
    rejected = invoke()
    assert rejected.status_code == 403 and rejected.json()['code'] == 'ADMIN_REQUIRED'
    if route_kind == 'service':
        rejected = client.get('/v1/admin/service-orders')
        assert rejected.status_code == 403 and rejected.json()['code'] == 'ADMIN_REQUIRED'
    app.state.settings.admin_emails = (auth['user']['email'],)
    rejected = invoke(**{'X-CSRF-Token': 'invalid'})
    assert rejected.status_code == 403 and rejected.json()['code'] == 'CSRF_REJECTED'
    rejected = invoke(Origin='https://untrusted.example')
    assert rejected.status_code == 403 and rejected.json()['code'] == 'ORIGIN_REJECTED'

    accepted = invoke()
    assert accepted.status_code == (201 if route_kind in {'icc', 'profile'} else 200), accepted.text
    if route_kind == 'structure':
        assert accepted.json()['data']['review_only'] is True
        assert accepted.json()['data']['production_enabled'] is False
    elif route_kind == 'profile':
        assert accepted.json()['data']['status'] == 'draft'
        assert accepted.json()['data']['test_only'] is True
    elif route_kind == 'service':
        row = accepted.json()['data']
        assert row['status'] == 'quoted' and len(row['quotes']) == 1
        assert row['credits_granted'] == 0 and row['payment_status'] == 'not_collected'
        listing = client.get('/v1/admin/service-orders')
        assert listing.status_code == 200
        assert any(item['id'] == order['id'] for item in listing.json()['data']['items'])
    # The bypass belongs only to those global administration endpoints.
    assert client.get(f"/v1/projects/{home_project['id']}").status_code == 403
