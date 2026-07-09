from django.urls import path
from . import views

urlpatterns = [
    path('health/', views.health_check, name='health_check'),
    path('cases/', views.CaseListCreateView.as_view(), name='case-list-create'),
    path('cases/<int:id>/', views.CaseDetailView.as_view(), name='case-detail'),
    path('gmail/sync/', views.GmailSyncView.as_view(), name='gmail-sync'),
    path('dashboard/stats/', views.DashboardStatsView.as_view(), name='dashboard-stats'),
    path('settings/translation-model/', views.TranslationModelView.as_view(),
         name='translation-model'),
]
