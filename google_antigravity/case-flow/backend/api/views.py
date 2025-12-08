from rest_framework import generics
from rest_framework.decorators import api_view
from rest_framework.response import Response
from .models import Case
from .serializers import CaseSerializer

@api_view(['GET'])
def health_check(request):
    return Response({"status": "ok", "message": "Backend is running!"})

class CaseListCreateView(generics.ListCreateAPIView):
    queryset = Case.objects.all().order_by('-created_at')
    serializer_class = CaseSerializer

class CaseDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Case.objects.all()
    serializer_class = CaseSerializer
    lookup_field = 'id'
