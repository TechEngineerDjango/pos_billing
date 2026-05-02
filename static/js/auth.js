/**
 * auth.js
 * 
 * SOLID Implementation for Authentication & Password Management.
 * 
 * Responsibility: Handles all frontend logic for user password operations.
 */

class PasswordService {
    constructor(formId) {
        this.formId = formId;
    }

    getForm() {
        return document.getElementById(this.formId);
    }

    async changePassword(formData) {
        const response = await fetch('/auth/change-password', {
            method: 'POST',
            body: formData,
            headers: {
                'Accept': 'application/json'
            }
        });
        
        const data = await response.json();
        return {
            status: response.status,
            data: data
        };
    }
}

function passwordChange() {
    const service = new PasswordService('changePasswordForm');
    
    return {
        open: false,
        error: '',
        successMsg: '',
        loading: false,

        openModal() {
            this.open = true;
            this.error = '';
            this.successMsg = '';
            // Reset fields
            if (this.$refs.new_pw) this.$refs.new_pw.value = '';
            if (this.$refs.confirm_pw) this.$refs.confirm_pw.value = '';
        },

        async submitForm() {
            const form = service.getForm();
            console.log('🌐 DIAGNOSTIC: Form element found:', !!form);
            
            const newPw = this.$refs.new_pw.value;
            const confirmPw = this.$refs.confirm_pw.value;
            console.log('🌐 DIAGNOSTIC: Input values present:', { newPw: !!newPw, confirmPw: !!confirmPw });

            if (newPw !== confirmPw) {
                this.error = 'New passwords do not match!';
                return;
            }

            this.loading = true;
            this.error = '';
            this.successMsg = '';

            try {
                const formData = new FormData(form);
                console.log('🌐 DIAGNOSTIC: FormData keys:', Array.from(formData.keys()));
                const result = await service.changePassword(formData);
                this.loading = false;

                if (result.status === 200) {
                    this.successMsg = result.data.message || 'Password updated successfully!';
                    form.reset();
                    setTimeout(() => { 
                        this.open = false; 
                        this.successMsg = ''; 
                    }, 2000);
                } else {
                    const detail = result.data.detail;
                    this.error = typeof detail === 'string' ? detail : JSON.stringify(detail);
                }
            } catch (err) {
                this.loading = false;
                this.error = 'Network error. Please check your connection.';
                console.error('Password change error:', err);
            }
        }
    };
}

// Global initialization
window.passwordChange = passwordChange;
