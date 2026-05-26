// ===== Main Application Entry Point =====
// Import and initialize all modules

import { initNavbar } from './navbar.js';
import { initTheme } from './theme.js';
import { initHero } from './hero.js';
import { initJobs } from './jobs.js';
import { initOffers } from './offers.js';
import { initContact } from './contact.js';
import { 
    initNotifications, 
    initPreloader, 
    initBackToTop, 
    initSmoothScroll, 
    initScrollReveal, 
    initCardEffects,
    initEasterEgg 
} from './utils.js';

// Initialize all modules when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    // Core functionality
    initPreloader();
    initNavbar();
    initTheme();
    initSmoothScroll();
    initNotifications();
    initBackToTop();
    
    // Section-specific functionality
    initHero();
    initJobs();
    initOffers();
    initContact();
    
    // Visual effects
    initScrollReveal();
    initCardEffects();
    
    // Fun stuff
    initEasterEgg();
    

});

