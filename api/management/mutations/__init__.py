from .alias import (
    create_alias,
    create_alias_rule,
    delete_alias,
    delete_alias_rule,
    update_alias,
)
from .bug_report import (
    delete_bug_report,
    file_bug_report_issue,
    forward_bug_report,
    resolve_bug_report,
    unresolve_bug_report,
)
from .collaboration_grant import revoke_collaboration_grant
from .device import create_device, delete_device, update_device
from .device_code import (
    accept_device_code,
    decline_device_code,
)
from .device_group import (
    add_device_to_group,
    create_device_group,
    delete_device_group,
    remove_device_from_group,
)
from .hub import delete_hub, probe_hub, update_hub
from .hub_device_code import (
    accept_hub_device_code,
    decline_hub_device_code,
)
from .invite import (
    accept_invite,
    cancel_invite,
    create_invite,
    decline_invite,
)
from .ionscale import (
    create_ionscale_auth_key,
    create_ionscale_layer,
    delete_ionscale_auth_key,
    delete_ionscale_layer,
    delete_machine,
    disable_tailnet_lock,
    enable_tailnet_lock,
    replace_ionscale_layer,
    update_ionscale_layer,
)
from .mandate import issue_mandate_key, revoke_mandate
from .membership import (
    approve_membership_request,
    decline_membership_request,
    delete_membership,
    request_membership,
    set_membership_brand_hue,
    update_membership,
)
from .mesh_device_code import (
    accept_mesh_device_code,
    decline_mesh_device_code,
)
from .notification import notify_member, set_membership_notifications
from .organization import (
    change_organization_owner,
    connect_kommunity_partner,
    create_organization,
    delete_organization,
    update_organization,
)
from .organization_profile import (
    create_organization_profile,
    delete_organization_profile,
    update_organization_profile,
)
from .profile import create_profile, delete_profile, update_profile
from .report import (
    request_client_report,
    resolve_report,
    unresolve_report,
)
from .revoke import (
    revoke_client_sessions,
    revoke_organization_sessions,
)
from .role_request import (
    approve_role_request,
    cancel_role_request,
    decline_role_request,
    request_role,
)
from .role_set import (
    create_role_set,
    delete_role_set,
    update_role_set,
)
from .upload import request_media_upload
