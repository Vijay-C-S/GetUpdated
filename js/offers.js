// ===== Offers Module =====
// Handles countdown timers for offers

export function initOffers() {
    // Countdown Timer
    function updateCountdowns() {
        const countdowns = document.querySelectorAll('.countdown');
        
        countdowns.forEach(countdown => {
            let time = countdown.textContent.split(':');
            let hours = parseInt(time[0]);
            let minutes = parseInt(time[1]);
            let seconds = parseInt(time[2]);
            
            seconds--;
            
            if (seconds < 0) {
                seconds = 59;
                minutes--;
            }
            
            if (minutes < 0) {
                minutes = 59;
                hours--;
            }
            
            if (hours < 0) {
                hours = 23;
            }
            
            countdown.textContent = `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
        });
    }

    setInterval(updateCountdowns, 1000);
}
