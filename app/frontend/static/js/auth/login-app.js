function loginApp() {
    return {
        username: '',
        password: '',
        loading: false,
        error: '',

        async login() {
            this.loading = true;
            this.error = '';

            const formData = new FormData();
            formData.append('username', this.username);
            formData.append('password', this.password);

            try {
                const response = await fetch('/auth/login', {
                    method: 'POST',
                    headers: {
                        'x-csrf-token': ApiClient.getCsrfToken()
                    },
                    body: formData,
                    redirect: 'follow'
                });

                if (response.ok || response.redirected) {
                    // Follow the redirect URL from server (role-based)
                    window.location.href = response.url;
                } else {
                    const data = await response.json();
                    this.error = data.detail || 'Login Failed';
                }
            } catch (e) {
                this.error = 'Network Error';
            } finally {
                this.loading = false;
            }
        }
    }
}
