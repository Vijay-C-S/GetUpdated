// ===== Contact Module =====
// Handles contact form submission

export function initContact() {
    const contactForm = document.getElementById('contact-form');

    if (contactForm) {
        contactForm.addEventListener('submit', (e) => {
            e.preventDefault();
            showNotification('✅ Message sent successfully! We\'ll get back to you soon.');
            contactForm.reset();
        });
    }
}

function validateEmail(email) {
    const re = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    return re.test(email);
}

function showNotification(message) {
    const event = new CustomEvent('showNotification', { detail: message });
    document.dispatchEvent(event);
}
