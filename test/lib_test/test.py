"""
Test utilities for Anna's Archive.

Provides:
- ViewTestMixin: Mixin for testing Flask views
- TestClient extensions
- Response assertions
"""

from flask import url_for


class ViewTestMixin:
    """
    Mixin for testing Flask views.

    Provides common test utilities for view testing.

    Usage:
        class TestMyView(ViewTestMixin):
            def test_view(self):
                response = self.client.get(url_for('my.view'))
                self.assert_view(response, 200)
    """

    def assert_view(self, response, status_code=200, **kwargs):
        """
        Assert view returns expected status.

        Args:
            response: Flask test response
            status_code: Expected status code
            **kwargs: Additional response attributes to check
        """
        assert response.status_code == status_code, \
            f"Expected {status_code}, got {response.status_code}"

        for key, value in kwargs.items():
            assert getattr(response, key) == value, \
                f"Expected {key}={value}, got {getattr(response, key)}"

    def assert_redirect(self, response, endpoint=None, **kwargs):
        """
        Assert view redirects to expected location.

        Args:
            response: Flask test response
            endpoint: Expected redirect endpoint
            **kwargs: Expected URL kwargs
        """
        assert response.status_code in (301, 302, 303, 307, 308), \
            f"Expected redirect, got {response.status_code}"

        if endpoint:
            expected_url = url_for(endpoint, **kwargs)
            assert expected_url in response.location, \
                f"Expected redirect to {expected_url}, got {response.location}"

    def assert_template(self, response, template_name):
        """
        Assert view uses expected template.

        Args:
            response: Flask test response
            template_name: Expected template name
        """
        assert template_name in response.template.name, \
            f"Expected template {template_name}, got {response.template.name}"

    def assert_context(self, response, **kwargs):
        """
        Assert view provides expected context.

        Args:
            response: Flask test response
            **kwargs: Expected context values
        """
        for key, value in kwargs.items():
            assert key in response.context, \
                f"Expected context key {key} not found"
            assert response.context[key] == value, \
                f"Expected {key}={value}, got {response.context[key]}"

    def assert_json(self, response, **kwargs):
        """
        Assert view returns expected JSON.

        Args:
            response: Flask test response
            **kwargs: Expected JSON values
        """
        assert response.is_json, "Response is not JSON"
        data = response.get_json()

        for key, value in kwargs.items():
            assert key in data, f"Expected JSON key {key} not found"
            assert data[key] == value, \
                f"Expected {key}={value}, got {data[key]}"

    def login_user(self, user):
        """
        Login a user for testing.

        Args:
            user: User object to login

        Note:
            Override this method to implement actual authentication
        """
        # Override in subclass with actual auth implementation
        pass

    def logout_user(self):
        """
        Logout current user.

        Note:
            Override this method to implement actual logout
        """
        # Override in subclass with actual auth implementation
        pass


class TestClientMixin:
    """
    Mixin for Flask test client extensions.

    Provides additional test client methods.
    """

    def get_json(self, url, **kwargs):
        """
        GET request expecting JSON response.

        Args:
            url: URL to request
            **kwargs: Additional kwargs for client.get()

        Returns:
            Parsed JSON response
        """
        response = self.client.get(url, **kwargs)
        assert response.is_json, f"Expected JSON, got {response.status_code}"
        return response.get_json()

    def post_json(self, url, data, **kwargs):
        """
        POST request with JSON data.

        Args:
            url: URL to request
            data: JSON data to post
            **kwargs: Additional kwargs for client.post()

        Returns:
            Parsed JSON response
        """
        response = self.client.post(
            url,
            json=data,
            **kwargs
        )
        return response

    def assert_bad_request(self, response):
        """Assert response is 400 Bad Request."""
        assert response.status_code == 400, \
            f"Expected 400, got {response.status_code}"

    def assert_unauthorized(self, response):
        """Assert response is 401 Unauthorized."""
        assert response.status_code == 401, \
            f"Expected 401, got {response.status_code}"

    def assert_forbidden(self, response):
        """Assert response is 403 Forbidden."""
        assert response.status_code == 403, \
            f"Expected 403, got {response.status_code}"

    def assert_not_found(self, response):
        """Assert response is 404 Not Found."""
        assert response.status_code == 404, \
            f"Expected 404, got {response.status_code}"

    def assert_method_not_allowed(self, response):
        """Assert response is 405 Method Not Allowed."""
        assert response.status_code == 405, \
            f"Expected 405, got {response.status_code}"