from django.test import TestCase
from unittest.mock import patch, PropertyMock

from corehq.apps.api.validation import WebUserResourceValidator
from corehq.apps.custom_data_fields.models import (
    CustomDataFieldsDefinition,
    CustomDataFieldsProfile,
)
from corehq.apps.domain.models import Domain
from corehq.apps.users.models import WebUser
from corehq.apps.users.views.mobile.custom_data_fields import UserFieldsView
from corehq.util.test_utils import flag_enabled, flag_disabled


class TestWebUserResourceValidator(TestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.domain = Domain(name="test-domain", is_active=True)
        cls.domain.save()
        cls.addClassCleanup(cls.domain.delete)
        cls.requesting_user = WebUser.create(cls.domain.name, "test@example.com", "123", None, None)
        cls.validator = WebUserResourceValidator(cls.domain.name, cls.requesting_user)
        cls.definition = CustomDataFieldsDefinition.get_or_create(cls.domain.name, UserFieldsView.field_type)
        cls.definition.profile_required_for_user_type = [UserFieldsView.WEB_USER]
        cls.definition.save()

    @classmethod
    def tearDownClass(cls):
        cls.requesting_user.delete(None, None)
        super().tearDownClass()

    def test_validate_parameters(self):
        params = {"email": "test@example.com", "role": "Admin"}
        self.assertIsNone(self.validator.validate_parameters(params, True))

        params = {"email": "test@example.com", "role": "Admin"}
        self.assertEqual(self.validator.validate_parameters(params, False), "Invalid parameter(s): email")

        invalid_params = {"invalid_param": "value"}
        self.assertEqual(self.validator.validate_parameters(invalid_params, True),
                         "Invalid parameter(s): invalid_param")
        self.assertEqual(self.validator.validate_parameters(invalid_params, False),
                         "Invalid parameter(s): invalid_param")

    @flag_enabled('TABLEAU_USER_SYNCING')
    @patch('corehq.apps.users.models.WebUser.has_permission', return_value=True)
    def test_validate_parameters_with_tableau_edit_permission(self, mock_has_permission):
        params = {"email": "test@example.com", "role": "Admin", "tableau_role": "Viewer"}
        self.assertIsNone(self.validator.validate_parameters(params, True))

    @flag_disabled('TABLEAU_USER_SYNCING')
    @patch('corehq.apps.users.models.WebUser.has_permission', return_value=False)
    def test_validate_parameters_without_tableau_edit_permission(self, mock_has_permission):
        params = {"email": "test@example.com", "role": "Admin", "tableau_role": "Viewer"}
        self.assertEqual(self.validator.validate_parameters(params, True),
                         "You do not have permission to edit Tableau Configuration.")

    @patch('corehq.apps.registration.validation.domain_has_privilege', return_value=True)
    def test_validate_parameters_with_profile_permission(self, mock_domain_has_privilege):
        params = {"email": "test@example.com", "role": "Admin", "profile": "some_profile"}
        self.assertIsNone(self.validator.validate_parameters(params, True))

    @patch('corehq.apps.registration.validation.domain_has_privilege', return_value=False)
    def test_validate_parameters_without_profile_permission(self, mock_domain_has_privilege):
        params = {"email": "test@example.com", "role": "Admin", "profile": "some_profile"}
        self.assertEqual(self.validator.validate_parameters(params, True),
                         "This domain does not have user profile privileges.")

    @patch('corehq.apps.registration.validation.domain_has_privilege', return_value=True)
    def test_validate_parameters_with_location_privilege(self, mock_domain_has_privilege):
        params = {"email": "test@example.com", "role": "Admin", "primary_location": "some_location"}
        self.assertIsNone(self.validator.validate_parameters(params, True))
        params = {"email": "test@example.com", "role": "Admin", "assigned_locations": "some_location"}
        self.assertIsNone(self.validator.validate_parameters(params, True))

    @patch('corehq.apps.registration.validation.domain_has_privilege', return_value=False)
    def test_validate_parameters_without_location_privilege(self, mock_domain_has_privilege):
        params = {"email": "test@example.com", "role": "Admin", "primary_location": "some_location"}
        self.assertEqual(self.validator.validate_parameters(params, True),
                         "This domain does not have locations privileges.")

        params = {"email": "test@example.com", "role": "Admin", "assigned_locations": "some_location"}
        self.assertEqual(self.validator.validate_parameters(params, True),
                         "This domain does not have locations privileges.")

    def test_validate_role(self):
        with patch.object(WebUserResourceValidator,
                          'roles_by_name', new_callable=PropertyMock) as mock_roles_by_name:
            mock_roles_by_name.return_value = {"Admin": "Admin_role_id"}
            self.assertIsNone(self.validator.validate_role("Admin"))

            mock_roles_by_name.return_value = {"User": "User_role_id"}
            self.assertEqual(self.validator.validate_role("Admin"),
                             "Role 'Admin' does not exist or you do not have permission to access it")

    def test_validate_role_with_no_role_input(self):
        with patch.object(WebUserResourceValidator,
                          'roles_by_name', new_callable=PropertyMock) as mock_roles_by_name:
            mock_roles_by_name.return_value = {"Admin": "Admin_role_id"}
            self.assertIsNone(self.validator.validate_role(None))

    def test_validate_profile_with_no_profile_input(self):
        with patch.object(WebUserResourceValidator,
                          'profiles_by_name', new_callable=PropertyMock) as mock_profiles_by_name:
            mock_profiles_by_name.return_value = {"fake_profile": CustomDataFieldsProfile(
                name="fake_profile",
                definition=self.definition
            )}
            self.assertIsNone(self.validator.validate_profile(None, False))
            self.assertEqual(self.validator.validate_profile(None, True),
                "A profile must be assigned to users of the following type(s): Web Users")

    def test_validate_email(self):
        self.assertIsNone(self.validator.validate_email("newtest@example.com", True))

        self.assertEqual(self.validator.validate_email("test@example.com", True),
                        "A user with this email address is already in "
                        "this project or has a pending invitation.")

        deactivated_user = WebUser.create(self.domain.name, "deactivated@example.com", "123", None, None)
        deactivated_user.is_active = False
        deactivated_user.save()
        self.assertEqual(self.validator.validate_email("deactivated@example.com", True),
                         "A user with this email address is deactivated. ")

    def test_validate_email_with_no_email_input(self):
        self.assertIsNone(self.validator.validate_email(None, True))

    def test_validate_locations(self):
        self.assertIsNone(self.validator.validate_locations(self.requesting_user.username,
                                                            None, None))
        self.assertEqual(
            self.validator.validate_locations(self.requesting_user.username, ["loc1", "loc2"], None),
            "Both primary_location and locations must be provided together."
        )
        self.assertEqual(
            self.validator.validate_locations(self.requesting_user.username, None, 'loc1'),
            "Both primary_location and locations must be provided together."
        )

        with patch('corehq.apps.user_importer.validation.LocationValidator.validate_spec') as mock_validate_spec:
            mock_validate_spec.return_value = None
            self.assertIsNone(self.validator.validate_locations(self.requesting_user.username,
                                                                ["loc1", "loc2"], "loc1"))

            actual_spec = mock_validate_spec.call_args[0][0]
            self.assertEqual(actual_spec['username'], self.requesting_user.username)
            self.assertCountEqual(actual_spec['location_code'], ["loc1", "loc2"])

        self.assertEqual(
            self.validator.validate_locations(self.requesting_user.username, ["loc1", "loc2"], "loc3"),
            "Primary location must be one of the user's locations"
        )

        self.assertEqual(
            self.validator.validate_locations(self.requesting_user.username, ["loc1", "loc2"], ""),
            "Primary location can't be empty if the user has any locations set"
        )
