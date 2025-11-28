package core

import (
	"context"
	"log"
	"sync"
	"time"

	"github.com/robfig/cron/v3"
)

// ScheduledJob represents a single scheduled workflow task
type ScheduledJob struct {
	ID        string
	Name      string
	CronExpr  string
	Workflow  *Workflow
	Node      *WorkflowNode
	EntryID   cron.EntryID
	Enabled   bool
	LastRun   time.Time
	NextRun   time.Time
	Mutex     sync.Mutex
}

// Scheduler is responsible for managing and executing scheduled jobs
type Scheduler struct {
	mu       sync.RWMutex
	engine   *Engine
	cron     *cron.Cron
	jobs     map[string]*ScheduledJob
	ctx      context.Context
	cancel   context.CancelFunc
}

// NewScheduler creates a new Scheduler instance
func NewScheduler(engine *Engine) *Scheduler {
	ctx, cancel := context.WithCancel(context.Background())
	return &Scheduler{
		engine: engine,
		cron:   cron.New(cron.WithSeconds()),
		jobs:   make(map[string]*ScheduledJob),
		ctx:    ctx,
		cancel: cancel,
	}
}

// AddJob schedules a workflow node to run on a cron expression
func (s *Scheduler) AddJob(job *ScheduledJob) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	if !job.Enabled {
		return nil
	}

	entryID, err := s.cron.AddFunc(job.CronExpr, func() {
		s.runJob(job)
	})
	if err != nil {
		log.Printf("Failed to add scheduled job %s: %v", job.Name, err)
		return err
	}

	job.EntryID = entryID
	s.jobs[job.ID] = job
	log.Printf("Scheduled job added: %s, Cron: %s", job.Name, job.CronExpr)
	return nil
}

// runJob executes a scheduled workflow node
func (s *Scheduler) runJob(job *ScheduledJob) {
	job.Mutex.Lock()
	defer job.Mutex.Unlock()

	job.LastRun = time.Now()
	log.Printf("Executing scheduled job: %s", job.Name)

	if job.Node != nil && job.Workflow != nil {
		// Emit event to engine to execute the workflow node
		event := Event{
			ID:      "scheduled-" + job.ID,
			Type:    "scheduled",
			Payload: map[string]interface{}{"job_name": job.Name, "timestamp": job.LastRun},
		}
		s.engine.Emit(event)
	}

	// Update next run time
	entry := s.cron.Entry(job.EntryID)
	job.NextRun = entry.Next
}

// RemoveJob removes a scheduled job from the scheduler
func (s *Scheduler) RemoveJob(jobID string) {
	s.mu.Lock()
	defer s.mu.Unlock()

	if job, ok := s.jobs[jobID]; ok {
		s.cron.Remove(job.EntryID)
		delete(s.jobs, jobID)
		log.Printf("Scheduled job removed: %s", job.Name)
	}
}

// Start the scheduler
func (s *Scheduler) Start() {
	s.cron.Start()
	log.Println("Scheduler started")
}

// Stop the scheduler gracefully
func (s *Scheduler) Stop() {
	ctx := s.cron.Stop() // stop cron scheduler
	<-ctx.Done()
	s.cancel()
	log.Println("Scheduler stopped gracefully")
}

// ListJobs returns all scheduled jobs
func (s *Scheduler) ListJobs() []*ScheduledJob {
	s.mu.RLock()
	defer s.mu.RUnlock()

	jobs := []*ScheduledJob{}
	for _, job := range s.jobs {
		jobs = append(jobs, job)
	}
	return jobs
}

// Example usage
func ExampleScheduler() {
	engine := NewEngine(100)
	engine.Start()

	scheduler := NewScheduler(engine)

	job := &ScheduledJob{
		ID:       "job1",
		Name:     "Test Cron Job",
		CronExpr: "*/10 * * * * *", // every 10 seconds
		Enabled:  true,
	}

	scheduler.AddJob(job)
	scheduler.Start()

	// Let scheduler run for 30 seconds
	time.Sleep(30 * time.Second)
	scheduler.Stop()
	engine.Stop()
}
