//=====MainApplicationEntryPoint=====
//Importandinitializeallmodules

import{initNavbar}from'./navbar.js';
import{initTheme}from'./theme.js';
import{initHero}from'./hero.js';
import{initJobs}from'./jobs.js';
import{initOffers}from'./offers.js';
import{initContact}from'./contact.js';
import{
initNotifications,
initPreloader,
initBackToTop,
initSmoothScroll,
initScrollReveal,
initCardEffects,
initEasterEgg
}from'./utils.js';

//InitializeallmoduleswhenDOMisready
document.addEventListener('DOMContentLoaded',()=>{
//Corefunctionality
initPreloader();
initNavbar();
initTheme();
initSmoothScroll();
initNotifications();
initBackToTop();

//Section-specificfunctionality
initHero();
initJobs();
initOffers();
initContact();

//Visualeffects
initScrollReveal();
initCardEffects();

//Funstuff
initEasterEgg();

//Consolebranding
console.log('%cGetUpdated🚀','font-size:24px;font-weight:bold;color:#6366f1;');
console.log('%cWelcometoGetUpdated-YourGatewaytoSuccess!','font-size:14px;color:#ec4899;');
});

