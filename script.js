//=====Preloader=====
constpreloader=document.getElementById('preloader');
if(preloader){
window.addEventListener('load',()=>{
setTimeout(()=>{
preloader.classList.add('hidden');
},2000);
});
}

//=====NavbarScrollEffect=====
constnavbar=document.getElementById('navbar');
if(navbar){
window.addEventListener('scroll',()=>{
if(window.scrollY>50){
navbar.classList.add('scrolled');
}else{
navbar.classList.remove('scrolled');
}
});
}

//=====MobileMenuToggle=====
consthamburger=document.getElementById('hamburger');
constnavMenu=document.getElementById('nav-menu');
constnavLinks=document.querySelectorAll('.nav-link');

if(hamburger&&navMenu){
hamburger.addEventListener('click',()=>{
hamburger.classList.toggle('active');
navMenu.classList.toggle('active');
});

navLinks.forEach(link=>{
link.addEventListener('click',()=>{
hamburger.classList.remove('active');
navMenu.classList.remove('active');
});
});
}

//=====ActiveNavLinkonScroll=====
constsections=document.querySelectorAll('section[id]');

if(sections.length>0&&navLinks.length>0){
window.addEventListener('scroll',()=>{
constscrollY=window.pageYOffset;

sections.forEach(section=>{
constsectionHeight=section.offsetHeight;
constsectionTop=section.offsetTop-100;
constsectionId=section.getAttribute('id');
constnavLink=document.querySelector(`.nav-link[href="#${sectionId}"]`);

if(navLink&&scrollY>sectionTop&&scrollY<=sectionTop+sectionHeight){
navLinks.forEach(link=>link.classList.remove('active'));
navLink.classList.add('active');
}
});
});
}

//=====ThemeToggle=====
constthemeToggle=document.getElementById('theme-toggle');
constbody=document.body;

//Checkforsavedthemepreference
constsavedTheme=localStorage.getItem('theme');
if(savedTheme){
body.setAttribute('data-theme',savedTheme);
if(themeToggle){
updateThemeIcon(savedTheme);
}
}

if(themeToggle){
themeToggle.addEventListener('click',()=>{
constcurrentTheme=body.getAttribute('data-theme');
constnewTheme=currentTheme==='dark'?'light':'dark';

body.setAttribute('data-theme',newTheme);
localStorage.setItem('theme',newTheme);
updateThemeIcon(newTheme);
});
}

functionupdateThemeIcon(theme){
if(themeToggle){
consticon=themeToggle.querySelector('i');
if(icon){
icon.className=theme==='dark'?'fasfa-sun':'fasfa-moon';
}
}
}

//=====CounterAnimation=====
constcounters=document.querySelectorAll('.stat-number');
constcounterObserver=newIntersectionObserver((entries)=>{
entries.forEach(entry=>{
if(entry.isIntersecting){
constcounter=entry.target;
consttarget=parseInt(counter.getAttribute('data-count'));
animateCounter(counter,target);
counterObserver.unobserve(counter);
}
});
},{threshold:0.5});

counters.forEach(counter=>counterObserver.observe(counter));

functionanimateCounter(element,target){
letcurrent=0;
constincrement=target/100;
constduration=2000;
conststepTime=duration/100;

consttimer=setInterval(()=>{
current+=increment;
if(current>=target){
element.textContent=target.toLocaleString();
clearInterval(timer);
}else{
element.textContent=Math.floor(current).toLocaleString();
}
},stepTime);
}

//=====JobsFilter=====
constfilterBtns=document.querySelectorAll('.filter-btn');
constjobCards=document.querySelectorAll('.job-card');

filterBtns.forEach(btn=>{
btn.addEventListener('click',()=>{
//Updateactivebutton
filterBtns.forEach(b=>b.classList.remove('active'));
btn.classList.add('active');

//Filterjobs
constfilter=btn.getAttribute('data-filter');

jobCards.forEach(card=>{
if(filter==='all'||card.getAttribute('data-type')===filter){
card.style.display='block';
card.style.animation='fadeIn0.5sease';
}else{
card.style.display='none';
}
});
});
});

//AddfadeInanimation
conststyle=document.createElement('style');
style.textContent=`
@keyframesfadeIn{
from{opacity:0;transform:translateY(20px);}
to{opacity:1;transform:translateY(0);}
}
`;
document.head.appendChild(style);

//=====JobDeadlineChecker=====
functioncheckJobDeadlines(){
constjobCardsWithDeadline=document.querySelectorAll('.job-card[data-deadline]');
constnow=newDate();

jobCardsWithDeadline.forEach(card=>{
constdeadlineStr=card.getAttribute('data-deadline');
constdeadline=newDate(deadlineStr);

if(now>deadline){
//Deadlinehaspassed-updatethecard
constdeadlineTag=card.querySelector('.deadline-tag')||card.querySelector('.tag[style*="color:#ef4444"]');
constapplyBtn=card.querySelector('.apply-btn');
constbadge=card.querySelector('.job-badge');

//Updatethedeadlinetag
if(deadlineTag){
deadlineTag.textContent='❌RegistrationClosed';
deadlineTag.style.background='#f3f4f6';
deadlineTag.style.color='#6b7280';
}

//Disableandgreyouttheapplybutton
if(applyBtn){
applyBtn.textContent='RegistrationClosed';
applyBtn.style.background='#9ca3af';
applyBtn.style.cursor='not-allowed';
applyBtn.style.pointerEvents='none';
applyBtn.removeAttribute('href');
}

//Updatethebadge
if(badge){
badge.textContent='⏰EXPIRED';
badge.style.background='linear-gradient(135deg,#6b7280,#4b5563)';
}

//Greyoutthecardborder
card.style.borderColor='#d1d5db';
card.classList.remove('hot-job');
card.classList.add('expired-job');
}
});
}

//Checkdeadlinesonpageloadandeveryminute
checkJobDeadlines();
setInterval(checkJobDeadlines,60000);

//=====BookmarkToggle=====
constbookmarkBtns=document.querySelectorAll('.bookmark-btn');

bookmarkBtns.forEach(btn=>{
btn.addEventListener('click',()=>{
consticon=btn.querySelector('i');
icon.classList.toggle('far');
icon.classList.toggle('fas');

if(icon.classList.contains('fas')){
showNotification('Jobbookmarkedsuccessfully!📌');
}else{
showNotification('Bookmarkremoved');
}
});
});

//=====NotificationSystem=====
functionshowNotification(message){
constnotification=document.createElement('div');
notification.className='notification';
notification.innerHTML=message;
notification.style.cssText=`
position:fixed;
bottom:100px;
right:30px;
padding:1rem1.5rem;
background:linear-gradient(135deg,#6366f1,#ec4899);
color:white;
border-radius:10px;
font-weight:500;
z-index:9999;
animation:slideIn0.3sease,slideOut0.3sease2.7s;
box-shadow:010px30pxrgba(99,102,241,0.4);
`;

document.body.appendChild(notification);

setTimeout(()=>{
notification.remove();
},3000);
}

//Addnotificationanimations
constnotifStyle=document.createElement('style');
notifStyle.textContent=`
@keyframesslideIn{
from{opacity:0;transform:translateX(100px);}
to{opacity:1;transform:translateX(0);}
}
@keyframesslideOut{
from{opacity:1;transform:translateX(0);}
to{opacity:0;transform:translateX(100px);}
}
`;
document.head.appendChild(notifStyle);

//=====CountdownTimer=====
functionupdateCountdowns(){
constcountdowns=document.querySelectorAll('.countdown');

countdowns.forEach(countdown=>{
lettime=countdown.textContent.split(':');
lethours=parseInt(time[0]);
letminutes=parseInt(time[1]);
letseconds=parseInt(time[2]);

seconds--;

if(seconds<0){
seconds=59;
minutes--;
}

if(minutes<0){
minutes=59;
hours--;
}

if(hours<0){
hours=23;
}

countdown.textContent=`${String(hours).padStart(2,'0')}:${String(minutes).padStart(2,'0')}:${String(seconds).padStart(2,'0')}`;
});
}

setInterval(updateCountdowns,1000);

//=====BacktoTopButton=====
constbackToTop=document.getElementById('back-to-top');

window.addEventListener('scroll',()=>{
if(window.scrollY>500){
backToTop.classList.add('visible');
}else{
backToTop.classList.remove('visible');
}
});

backToTop.addEventListener('click',()=>{
window.scrollTo({
top:0,
behavior:'smooth'
});
});

//=====FormSubmissions=====
constnewsletterForm=document.getElementById('newsletter-form');
constcontactForm=document.getElementById('contact-form');

newsletterForm.addEventListener('submit',(e)=>{
e.preventDefault();
constemail=newsletterForm.querySelector('input').value;

if(validateEmail(email)){
showNotification('🎉Successfullysubscribed!Welcomeaboard!');
newsletterForm.reset();
}else{
showNotification('❌Pleaseenteravalidemailaddress');
}
});

contactForm.addEventListener('submit',(e)=>{
e.preventDefault();
showNotification('✅Messagesentsuccessfully!We\'llgetbacktoyousoon.');
contactForm.reset();
});

functionvalidateEmail(email){
constre=/^[^\s@]+@[^\s@]+\.[^\s@]+$/;
returnre.test(email);
}

//=====ScrollRevealAnimation=====
constrevealElements=document.querySelectorAll('.service-card,.tech-card,.job-card,.income-card,.offer-card,.testimonial-card');

constrevealObserver=newIntersectionObserver((entries)=>{
entries.forEach(entry=>{
if(entry.isIntersecting){
entry.target.style.opacity='1';
entry.target.style.transform='translateY(0)';
revealObserver.unobserve(entry.target);
}
});
},{threshold:0.1,rootMargin:'0px0px-50px0px'});

revealElements.forEach(element=>{
element.style.opacity='0';
element.style.transform='translateY(30px)';
element.style.transition='opacity0.6sease,transform0.6sease';
revealObserver.observe(element);
});

//=====ParallaxEffectonHero=====
window.addEventListener('scroll',()=>{
constscrolled=window.pageYOffset;
constshapes=document.querySelectorAll('.shape');

shapes.forEach((shape,index)=>{
constspeed=(index+1)*0.1;
shape.style.transform=`translateY(${scrolled*speed}px)`;
});
});

//=====TypingEffectforHeroTitle=====
constheroTitle=document.querySelector('.hero-title');
if(heroTitle){
constspans=heroTitle.querySelectorAll('.gradient-text');
spans.forEach((span,index)=>{
span.style.animation=`fadeInUp0.8sease${index*0.3}sforwards`;
span.style.opacity='0';
});
}

//Addtypinganimation
consttypingStyle=document.createElement('style');
typingStyle.textContent=`
@keyframesfadeInUp{
from{
opacity:0;
transform:translateY(30px);
}
to{
opacity:1;
transform:translateY(0);
}
}
`;
document.head.appendChild(typingStyle);

//=====SmoothScrollforNavigationLinks=====
document.querySelectorAll('a[href^="#"]').forEach(anchor=>{
anchor.addEventListener('click',function(e){
e.preventDefault();
consttarget=document.querySelector(this.getAttribute('href'));
if(target){
constoffsetTop=target.offsetTop-80;
window.scrollTo({
top:offsetTop,
behavior:'smooth'
});
}
});
});

//=====CardHoverEffect=====
constcards=document.querySelectorAll('.service-card,.job-card,.income-card,.offer-card');

cards.forEach(card=>{
card.addEventListener('mousemove',(e)=>{
constrect=card.getBoundingClientRect();
constx=e.clientX-rect.left;
consty=e.clientY-rect.top;

constcenterX=rect.width/2;
constcenterY=rect.height/2;

constrotateX=(y-centerY)/20;
constrotateY=(centerX-x)/20;

card.style.transform=`perspective(1000px)rotateX(${rotateX}deg)rotateY(${rotateY}deg)translateY(-10px)`;
});

card.addEventListener('mouseleave',()=>{
card.style.transform='perspective(1000px)rotateX(0)rotateY(0)translateY(0)';
});
});

//=====ServiceWorkerRegistration(forPWA)=====
if('serviceWorker'innavigator){
window.addEventListener('load',()=>{
//ServiceworkercanberegisteredhereforPWAfunctionality
console.log('GetUpdatedwebsiteloadedsuccessfully!🚀');
});
}

//=====EasterEgg-KonamiCode=====
constkonamiCode=['ArrowUp','ArrowUp','ArrowDown','ArrowDown','ArrowLeft','ArrowRight','ArrowLeft','ArrowRight','b','a'];
letkonamiIndex=0;

document.addEventListener('keydown',(e)=>{
if(e.key===konamiCode[konamiIndex]){
konamiIndex++;
if(konamiIndex===konamiCode.length){
showNotification('🎮KonamiCodeactivated!Youfoundthesecret!🎉');
document.body.style.animation='rainbow2sease';
konamiIndex=0;
}
}else{
konamiIndex=0;
}
});

//Rainbowanimation
constrainbowStyle=document.createElement('style');
rainbowStyle.textContent=`
@keyframesrainbow{
0%{filter:hue-rotate(0deg);}
100%{filter:hue-rotate(360deg);}
}
`;
document.head.appendChild(rainbowStyle);

//=====InitializeEverything=====
document.addEventListener('DOMContentLoaded',()=>{
console.log('%cGetUpdated🚀','font-size:24px;font-weight:bold;color:#6366f1;');
console.log('%cWelcometoGetUpdated-YourGatewaytoSuccess!','font-size:14px;color:#ec4899;');
});

