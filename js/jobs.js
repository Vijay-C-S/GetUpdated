// ===== Jobs Module =====
// Handles job filtering and bookmark functionality

export function initJobs() {
    // Jobs Filter
    const filterBtns = document.querySelectorAll('.filter-btn');
    const jobCards = document.querySelectorAll('.job-card');

    filterBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            // Update active button
            filterBtns.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            
            // Filter jobs
            const filter = btn.getAttribute('data-filter');
            
            jobCards.forEach(card => {
                if (filter === 'all' || card.getAttribute('data-type') === filter) {
                    card.style.display = 'block';
                    card.style.animation = 'fadeIn 0.5s ease';
                } else {
                    card.style.display = 'none';
                }
            });
        });
    });

    // Bookmark Toggle
    const bookmarkBtns = document.querySelectorAll('.bookmark-btn');

    bookmarkBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            const icon = btn.querySelector('i');
            icon.classList.toggle('far');
            icon.classList.toggle('fas');
            
            if (icon.classList.contains('fas')) {
                showNotification('Job bookmarked successfully! 📌');
            } else {
                showNotification('Bookmark removed');
            }
        });
    });
}

// Add fadeIn animation
const style = document.createElement('style');
style.textContent = `
    @keyframes fadeIn {
        from { opacity: 0; transform: translateY(20px); }
        to { opacity: 1; transform: translateY(0); }
    }
`;
document.head.appendChild(style);

// Notification helper (will be imported from utils)
function showNotification(message) {
    const event = new CustomEvent('showNotification', { detail: message });
    document.dispatchEvent(event);
}
